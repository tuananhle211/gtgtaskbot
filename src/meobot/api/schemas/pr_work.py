"""What the Work API sends and accepts. M1.

Its own module rather than more of ``schemas/pr.py``, which is already three
thousand lines: the Work Ledger is a separate additive module and its wire
shapes have no reader in common with content's.

Two rules run through every model here
---------------------------------------

**No status arrives from a client.** There is no ``status`` field on any request
body, and no ``PATCH`` that could set one. Every lifecycle move is its own
endpoint calling its own service method, so there is no request that can file
work as already accepted or already approved - which is the difference between
an anti-gaming rule and a suggestion.

**No score, anywhere.** No ``base_score``, no multiplier, no quality grade, no
quota, no cap, no ``score_status``. ``count_status`` is as far as M1 goes, and
its three values say whether work is valid completed work - never what it is
worth.

Labels
------

Every ``*_label`` is composed here from
:mod:`meobot.domain.pr.work_labels`, so there is one Vietnamese wording per
value and the browser holds no second copy of the table.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from meobot.application.pr_work_bulk_validation_service import (
    BULK_VALIDATION_MAX_ITEMS,
    BulkValidationCandidate,
    BulkValidationOutcome,
    BulkValidationPreflight,
)
from meobot.application.pr_work_maintenance_service import TerminalDeleteEligibility
from meobot.application.pr_work_query_service import WorkPage, WorkSummary
from meobot.application.pr_work_readiness_service import WorkReadiness
from meobot.application.pr_work_result_service import ContainerSummary
from meobot.application.pr_work_service import WorkItemDetail
from meobot.db.models.pr_work import (
    PrWorkContribution,
    PrWorkEvidence,
    PrWorkHistory,
    PrWorkItem,
    PrWorkType,
)
from meobot.db.models.pr_work_result import PrWorkResult
from meobot.domain.pr.content_work import is_auto_work_type_code
from meobot.domain.pr.labels import priority_label
from meobot.domain.pr.models import PrPriority
from meobot.domain.pr.work import (
    EVIDENCE_TEXT_ONLY_LOCATION,
    PrWorkAssignmentMode,
    PrWorkCategory,
    PrWorkContributionRole,
    PrWorkUnit,
)
from meobot.domain.pr.work_labels import (
    bulk_validation_reason_label,
    count_status_label,
    work_category_label,
    work_contribution_role_label,
    work_event_label,
    work_exclusion_kind_label,
    work_result_source_label,
    work_result_status_label,
    work_source_label,
    work_status_label,
    work_unit_label,
)
from meobot.domain.pr.work_quota import PrWorkQuotaBasis
from meobot.domain.pr.work_quota_labels import (
    quota_workload_status_label,
    work_quota_basis_label,
)
from meobot.domain.pr.work_results import (
    PrWorkResultSource,
    may_reconsider,
    source_may_restore,
)


class _Body(BaseModel):
    """Request bodies refuse unknown fields.

    ``extra="forbid"`` rather than ``ignore``, and it is load-bearing here: a
    body carrying ``status``, ``count_status`` or ``counted_at`` is **refused**
    rather than silently dropped, so an attempt to set one is an error somebody
    sees rather than a no-op they might believe worked.
    """

    model_config = ConfigDict(extra="forbid")


# --- Work types -------------------------------------------------------------


class WorkTypeResponse(BaseModel):
    """One kind of work. Configuration, with no rate attached to it."""

    id: uuid.UUID
    #: The stable machine identifier. Nothing authorises or groups on ``name``.
    code: str
    name: str
    category: str
    category_label: str
    description: str | None = None
    default_unit: str
    default_unit_label: str
    #: **How this kind of work is measured against a KPI quota.** M2.
    #: ``ITEM_COUNT`` counts contributions; ``QUANTITY`` reads the work item's
    #: quantity, so "100 comments" is 100 rather than 1. Still no rate and no
    #: point value - what the work is *worth* is M6's.
    default_quota_basis: str
    default_quota_basis_label: str
    requires_evidence: bool
    is_active: bool
    display_order: int
    #: **M2.5.** Whether any work item, quota or allocation refers to this type.
    #: ``None`` on a list response, where answering it per row would be one
    #: query per type for information a picker does not use - the configuration
    #: screen asks per type when it opens an editor.
    is_in_use: bool | None = None
    #: **M2.5.** Whether ``code``, ``default_quota_basis`` and ``default_unit``
    #: are refused. Sent so the screen can draw them disabled with a reason;
    #: the server refuses them regardless of what the screen drew.
    structure_locked: bool | None = None
    #: Whether the content projector created this type, rather than a person.
    #: Read off the reserved code namespace, which manual creation refuses - so
    #: the flag is a fact about the row and not a label somebody set. Such a
    #: type arrives with **no scoring rule**; a screen should say so rather than
    #: show zero minutes.
    auto_provisioned: bool = False

    @classmethod
    def from_row(cls, row: PrWorkType, *, is_in_use: bool | None = None) -> WorkTypeResponse:
        return cls(
            is_in_use=is_in_use,
            structure_locked=is_in_use,
            auto_provisioned=is_auto_work_type_code(row.code),
            id=row.id,
            code=row.code,
            name=row.name,
            category=row.category.value,
            category_label=work_category_label(row.category),
            description=row.description,
            default_unit=row.default_unit.value,
            default_unit_label=work_unit_label(row.default_unit),
            default_quota_basis=row.default_quota_basis.value,
            default_quota_basis_label=work_quota_basis_label(row.default_quota_basis),
            requires_evidence=row.requires_evidence,
            is_active=row.is_active,
            display_order=row.display_order,
        )


class CreateWorkTypeRequest(_Body):
    """Register a kind of work. ``PR_WORK_CONFIGURE``."""

    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    category: PrWorkCategory
    description: str | None = Field(default=None, max_length=4000)
    #: Optional, and the omission means different things per basis - see
    #: :meth:`~meobot.application.pr_work_service.PrWorkService.create_work_type`.
    #: An ``ITEM_COUNT`` type gets ``ITEM``; a ``QUANTITY`` type is refused
    #: without one, because its unit is what its numbers mean.
    default_unit: PrWorkUnit | None = None
    #: M2. Defaults to ``ITEM_COUNT`` - one counted contribution, one unit -
    #: which is what every M1 figure already implied. A type whose work is
    #: genuinely measured by quantity is configured as such deliberately, and
    #: never guessed from ``default_unit``.
    default_quota_basis: PrWorkQuotaBasis = PrWorkQuotaBasis.ITEM_COUNT
    requires_evidence: bool = False
    display_order: int = Field(default=0, ge=0)


class UpdateWorkTypeRequest(_Body):
    """Edit a work type. **M2.5 replaced what may be edited, and when.**

    ``code``, ``default_unit`` and ``default_quota_basis`` are present now, and
    that is not a loosening: M1 left them off the body, which made a typo in a
    type created five minutes ago permanent. They are accepted here and
    **refused by the server once the type has been used** - see
    :meth:`~meobot.application.pr_work_service.PrWorkService.update_work_type`.
    Sending one on a used type is a ``409`` naming the field, never a silent
    no-op.

    ``is_active`` is **gone on purpose**. Retiring a kind of work is its own
    decision, and a generic body field is how it would end up flipped by a
    screen that meant to rename something. It has its own two routes and its own
    two audit actions.
    """

    code: str | None = Field(default=None, min_length=1, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    #: Safe metadata in M2.5, unlike M1: a category is how a report *groups*
    #: rows, not what a row measured, so re-grouping is a presentation change.
    category: PrWorkCategory | None = None
    description: str | None = Field(default=None, max_length=4000)
    default_unit: PrWorkUnit | None = None
    default_quota_basis: PrWorkQuotaBasis | None = None
    requires_evidence: bool | None = None
    display_order: int | None = Field(default=None, ge=0)


class BootstrapWorkTypesResponse(BaseModel):
    """What a bootstrap run did. **M2.5.**

    ``created`` is empty on every run after the first, which is the point: the
    caller can say "đã có sẵn" rather than guessing whether anything happened.
    """

    created: list[WorkTypeResponse]
    #: The whole taxonomy afterwards, so a screen refreshes from one response.
    work_types: list[WorkTypeResponse]


# --- Contributions ----------------------------------------------------------


class WorkContributionResponse(BaseModel):
    """One person's share of one job. **The KPI-facing row.**

    ``count_status`` says whether this person's share is valid completed work.
    It is ``PENDING`` until somebody who did not do the work approves the item,
    which is the anti-gaming boundary rendered as a field.

    There is no score here and there is nowhere to put one.
    """

    id: uuid.UUID
    work_item_id: uuid.UUID
    user_id: uuid.UUID
    #: Resolved server-side. A browser is never handed a bare UUID to print.
    user_name: str | None = None
    contribution_role: str
    contribution_role_label: str
    #: A share of one job. ``1.0`` for everybody by default - never divided by
    #: the number of contributors.
    credit_weight: Decimal
    assigned_at: datetime
    #: ``PENDING`` | ``COUNTED`` | ``EXCLUDED``.
    count_status: str
    count_status_label: str
    #: The period-attribution instant. A contribution belongs to the reporting
    #: period containing this, not the one it was assigned or completed in.
    counted_at: datetime | None = None
    excluded_reason: str | None = None

    @classmethod
    def from_row(
        cls, row: PrWorkContribution, *, names: dict[uuid.UUID, str] | None = None
    ) -> WorkContributionResponse:
        return cls(
            id=row.id,
            work_item_id=row.work_item_id,
            user_id=row.user_id,
            user_name=(names or {}).get(row.user_id),
            contribution_role=row.contribution_role.value,
            contribution_role_label=work_contribution_role_label(row.contribution_role),
            credit_weight=row.credit_weight,
            assigned_at=row.assigned_at,
            count_status=row.count_status.value,
            count_status_label=count_status_label(row.count_status),
            counted_at=row.counted_at,
            excluded_reason=row.excluded_reason,
        )


# --- Evidence and history ---------------------------------------------------


class WorkEvidenceResponse(BaseModel):
    """A text, or a link, proving the work. MeoBot stores no files - see the model.

    Two shapes share the row. A row written as **one free text** carries it in
    ``text`` (the member's words, links and all), a derived ``label``, and a
    ``location`` that is the first link in the text or ``null`` when there is
    none. A **legacy** row written as a label and a link carries both and no
    ``text``. A screen renders ``text`` when present and the label-as-link
    otherwise, so history stays readable without a rewrite.
    """

    id: uuid.UUID
    work_item_id: uuid.UUID
    label: str
    #: A URL or a path, or ``null`` for a text-only row. Never the sentinel.
    location: str | None
    note: str | None = None
    #: The member's own text, for rows written as one. ``None`` on legacy rows.
    text: str | None = None
    added_by_user_id: uuid.UUID
    created_at: datetime

    @classmethod
    def from_row(cls, row: PrWorkEvidence) -> WorkEvidenceResponse:
        text_only = row.location == EVIDENCE_TEXT_ONLY_LOCATION
        return cls(
            id=row.id,
            work_item_id=row.work_item_id,
            label=row.label,
            location=None if text_only else row.location,
            note=row.note,
            text=row.note,
            added_by_user_id=row.added_by_user_id,
            created_at=row.created_at,
        )


class WorkHistoryResponse(BaseModel):
    """One line of the user-facing timeline.

    The **domain** history, not the audit trail. It carries the status edge and
    the note somebody typed; the request id and the structured before/after are
    in ``audit_logs`` for whoever is investigating rather than reading.
    """

    id: uuid.UUID
    event_type: str
    event_label: str
    from_status: str | None = None
    to_status: str | None = None
    actor_user_id: uuid.UUID | None = None
    actor_name: str | None = None
    note: str | None = None
    created_at: datetime

    @classmethod
    def from_row(
        cls, row: PrWorkHistory, *, names: dict[uuid.UUID, str] | None = None
    ) -> WorkHistoryResponse:
        return cls(
            id=row.id,
            event_type=row.event_type.value,
            event_label=work_event_label(row.event_type),
            from_status=row.from_status.value if row.from_status else None,
            to_status=row.to_status.value if row.to_status else None,
            actor_user_id=row.actor_user_id,
            actor_name=(names or {}).get(row.actor_user_id) if row.actor_user_id else None,
            note=row.note,
            created_at=row.created_at,
        )


# --- Work items -------------------------------------------------------------


# --- Period containers and results -------------------------------------------


class WorkResultResponse(BaseModel):
    """One result declared into a period container.

    ``status`` is M1's count vocabulary at result grain: ``PENDING`` until
    somebody who is not the subject counts it, ``COUNTED`` when it adds to the
    actual, ``EXCLUDED`` when it is out. ``exclusion_kind`` says **why** it is
    out (``0041``) - ``VALIDATOR_REJECTED``, ``ADMIN_REMOVED``,
    ``SOURCE_REVERSED``, or ``null`` on a legacy row - and ``status_label``
    already reads accordingly, so a screen never maps the enum itself.

    The ``can_*`` flags are the server's answer from the same checks the
    writes make; a client that draws a button anyway meets the same refusal.
    """

    id: uuid.UUID
    work_item_id: uuid.UUID
    user_id: uuid.UUID
    quantity: Decimal
    label: str | None = None
    link: str | None = None
    note: str | None = None
    source_type: str
    source_label: str
    source_key: str | None = None
    #: The content item a ``CONTENT`` result came from, resolved server-side
    #: from the key, so a screen can offer *"Đồng bộ lại từ Nội dung"* for
    #: exactly that piece. ``None`` for every other source.
    content_id: uuid.UUID | None = None
    status: str
    status_label: str
    reported_by_user_id: uuid.UUID
    reported_by_name: str | None = None
    reported_at: datetime
    counted_at: datetime | None = None
    counted_by_user_id: uuid.UUID | None = None
    counted_by_name: str | None = None
    excluded_at: datetime | None = None
    excluded_by_user_id: uuid.UUID | None = None
    excluded_by_name: str | None = None
    excluded_reason: str | None = None
    #: ``VALIDATOR_REJECTED`` | ``ADMIN_REMOVED`` | ``SOURCE_REVERSED`` | null.
    exclusion_kind: str | None = None
    exclusion_kind_label: str | None = None
    #: True when no projection - manual or automatic, single or batch - will
    #: restore this row: a validator rejected it (or nobody recorded who
    #: excluded it). *Đồng bộ lại từ Nội dung* is not the way back; *Xem xét lại* is.
    held_by_validator: bool = False
    #: For a **pending source-derived** row: does the source still back it,
    #: as of this read? ``False`` means the projector has not swept it yet
    #: and a validator must not treat it as an ordinary pending result -
    #: ``can_validate`` / ``can_reject`` are already false. ``null`` for a
    #: manual result, a non-pending row, or a source with no opinion.
    source_eligible: bool | None = None
    #: Server-decided: the reporter may withdraw their own pending manual result.
    can_withdraw: bool = False
    #: Server-decided, from ``can_validate_results``: the actor may count or
    #: reject this row (pending or counted), or release a rejected one.
    can_validate: bool = False
    can_reject: bool = False
    can_reconsider: bool = False

    @classmethod
    def from_row(
        cls,
        row: PrWorkResult,
        *,
        names: dict[uuid.UUID, str] | None = None,
        actor_id: uuid.UUID | None = None,
        can_validate: bool = False,
        source_eligible: bool | None = None,
    ) -> WorkResultResponse:
        from meobot.domain.pr.content_work import content_work_source_entity
        from meobot.domain.pr.work import PrWorkCountStatus

        excluded = row.status is PrWorkCountStatus.EXCLUDED
        # A pending row the source no longer backs is not a pending result to
        # act on; the projector's sweep - or the validator's own guard - will
        # take it out.
        actionable = source_eligible is not False
        return cls(
            id=row.id,
            work_item_id=row.work_item_id,
            user_id=row.user_id,
            quantity=row.quantity,
            label=row.label,
            link=row.link,
            note=row.note,
            source_type=row.source_type.value,
            source_label=work_result_source_label(row.source_type),
            source_key=row.source_key,
            content_id=(
                content_work_source_entity(row.source_key)
                if row.source_type is PrWorkResultSource.CONTENT
                else None
            ),
            status=row.status.value,
            status_label=work_result_status_label(row.status, row.exclusion_kind),
            reported_by_user_id=row.reported_by_user_id,
            reported_by_name=(names or {}).get(row.reported_by_user_id),
            reported_at=row.reported_at,
            counted_at=row.counted_at,
            counted_by_user_id=row.counted_by_user_id,
            counted_by_name=(
                (names or {}).get(row.counted_by_user_id) if row.counted_by_user_id else None
            ),
            excluded_at=row.excluded_at,
            excluded_by_user_id=row.excluded_by_user_id,
            excluded_by_name=(
                (names or {}).get(row.excluded_by_user_id) if row.excluded_by_user_id else None
            ),
            excluded_reason=row.excluded_reason,
            exclusion_kind=row.exclusion_kind.value if row.exclusion_kind else None,
            exclusion_kind_label=(
                work_exclusion_kind_label(row.exclusion_kind) if row.exclusion_kind else None
            ),
            held_by_validator=excluded and not source_may_restore(row.exclusion_kind),
            source_eligible=source_eligible,
            can_withdraw=(
                actor_id is not None
                and row.reported_by_user_id == actor_id
                and row.source_type is PrWorkResultSource.MANUAL
                and row.status is PrWorkCountStatus.PENDING
            ),
            can_validate=can_validate and actionable and row.status is PrWorkCountStatus.PENDING,
            can_reject=can_validate and actionable and not excluded,
            can_reconsider=can_validate and excluded and may_reconsider(row.exclusion_kind),
        )


class PeriodContainerResponse(BaseModel):
    """One stream's month. **Every figure is computed on the server.**

    ``actual_quantity`` is what a validator counted; ``declared_quantity`` is
    what the employee reported, counted or not. ``target_quantity`` is the KPI
    read beside the actual and nothing else: ``completion_percent`` is uncapped
    (135.0 for 27 against 20) and ``null`` when there is no target, and
    ``standard_minutes`` prices the whole actual whatever the target says.
    ``progress_percent`` is the bar - capped at 100 - and the only figure here
    a screen may draw as a length.
    """

    period_id: uuid.UUID
    period_code: str
    period_status: str
    subject_user_id: uuid.UUID
    subject_name: str | None = None
    unit: str
    unit_label: str
    actual_quantity: Decimal
    declared_quantity: Decimal
    pending_quantity: Decimal
    excluded_quantity: Decimal
    result_count: int
    pending_count: int
    target_quantity: Decimal | None = None
    has_target: bool = False
    completion_percent: Decimal | None = None
    over_target_quantity: Decimal
    remaining_quantity: Decimal
    is_target_met: bool = False
    progress_percent: Decimal
    #: *"27 / 20 khách hàng"* or *"3 buổi"*. Composed here so a card, a
    #: Telegram tool and an export say the same sentence.
    actual_label: str
    standard_minutes: Decimal | None = None
    standard_minutes_per_unit: Decimal | None = None
    scoring_status: str
    scoring_status_label: str

    @classmethod
    def from_summary(
        cls, summary: ContainerSummary, *, names: dict[uuid.UUID, str] | None = None
    ) -> PeriodContainerResponse:
        comparison = summary.comparison
        target = summary.target_quantity
        actual = _plain(summary.counted_quantity)
        actual_label = (
            f"{actual} / {_plain(target)} {summary.unit_label}"
            if target is not None
            else f"{actual} {summary.unit_label}"
        )
        progress = (
            min(comparison.completion_percent, Decimal("100"))
            if comparison.completion_percent is not None
            else Decimal("0")
        )
        return cls(
            period_id=summary.period_id,
            period_code=summary.period_code,
            period_status=summary.period_status.value,
            subject_user_id=summary.subject_user_id,
            subject_name=(names or {}).get(summary.subject_user_id),
            unit=summary.unit.value,
            unit_label=summary.unit_label,
            actual_quantity=summary.counted_quantity,
            declared_quantity=summary.declared_quantity,
            pending_quantity=summary.pending_quantity,
            excluded_quantity=summary.excluded_quantity,
            result_count=summary.result_count,
            pending_count=summary.pending_count,
            target_quantity=target,
            has_target=comparison.has_target,
            completion_percent=comparison.completion_percent,
            over_target_quantity=comparison.over_target,
            remaining_quantity=comparison.remaining,
            is_target_met=comparison.is_met,
            progress_percent=progress,
            actual_label=actual_label,
            standard_minutes=summary.standard_minutes,
            standard_minutes_per_unit=summary.standard_minutes_per_unit,
            scoring_status=summary.scoring_status.value,
            scoring_status_label=quota_workload_status_label(summary.scoring_status.value),
        )


def _plain(value: Decimal | None) -> str:
    """``27`` rather than ``27.00``; ``0.5`` stays ``0.5``."""
    if value is None:
        return ""
    text = format(value.normalize(), "f")
    return text


class WorkItemResponse(BaseModel):
    """One real job, as a card or a row draws it.

    The five timestamps are five different facts and are sent as five fields:
    ``accepted_at`` is when it entered somebody's workload, ``completed_at``
    when a contributor said it was finished, and ``approved_at`` when somebody
    else confirmed that. Only the third can make a contribution count.
    """

    id: uuid.UUID
    code: str
    title: str
    description: str | None = None
    work_type_id: uuid.UUID
    work_type_code: str | None = None
    work_type_name: str | None = None
    work_type_category: str | None = None
    #: ``MANUAL`` in M1, always. The other values exist for M3's content
    #: projector and M4's recurring generator.
    source_type: str
    source_label: str
    status: str
    status_label: str
    priority: str
    priority_label: str
    #: "100 comments" is one item with ``quantity = 100``, never a hundred rows.
    quantity: Decimal | None = None
    unit: str | None = None
    unit_label: str | None = None
    due_at: datetime | None = None
    #: **When the work was performed, or is scheduled to be.** Post-M4.
    #:
    #: What a card prints as *"Thực hiện"*, and the field a monthly list is
    #: ordered by. Present for content-derived work - M3.1's canonical milestone,
    #: the head's approval for a script and the hand-in of a cut - and for
    #: recurring work, where it is the occurrence's own scheduled instant.
    #:
    #: **Null for manual work, and the client must render nothing when it is.**
    #: A person filing a job states a deadline and never says which day they
    #: will do it, so there is no execution date to show; printing ``due_at``
    #: under that heading would label a deadline as a performance, and printing
    #: ``created_at`` would label the moment the row was typed as one. Both are
    #: the invention this field exists to remove.
    execution_at: datetime | None = None
    #: True right now, and **independent of any period filter** the caller
    #: applied. Computed server-side so no browser compares a deadline to its
    #: own clock.
    is_overdue: bool = False
    created_by_user_id: uuid.UUID
    assigned_by_user_id: uuid.UUID | None = None
    assigned_at: datetime | None = None
    accepted_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    approved_at: datetime | None = None
    approved_by_user_id: uuid.UUID | None = None
    cancelled_at: datetime | None = None
    cancel_reason: str | None = None
    channel_id: uuid.UUID | None = None
    #: M3. The content item this work was projected from, when it was. ``None``
    #: for everything a person filed.
    content_id: uuid.UUID | None = None
    #: ``CNT-2026-000042``, so a work card can name the piece rather than a UUID
    #: and link to it. Resolved server-side, in one query for a whole page.
    content_code: str | None = None
    #: **M4B, surfaced post-M4.** The firing this job was generated by, and
    #: through it the template. Null for everything a recurring template did not
    #: produce. A plain id rather than something decoded out of ``source_key`` -
    #: see the column.
    recurring_occurrence_id: uuid.UUID | None = None
    #: The template's own id and name, for *"đi tới công việc định kỳ"*.
    #: Resolved server-side in one query for a whole page, like ``content_code``
    #: beside it, so no card issues its own request.
    recurring_template_id: uuid.UUID | None = None
    recurring_template_name: str | None = None
    #: **True when the content workflow owns this row.** The client draws
    #: *"Nguồn: Nội dung"* and hides every edit control from it, and the server
    #: refuses the same edits either way - see
    #: ``PrWorkService._refuse_source_mutation``. Hiding a control is a courtesy;
    #: the refusal is the rule.
    is_source_derived: bool = False
    #: **Period-container patch.** True for a stream that accumulates results
    #: by month. The client draws the result controls and the actual-vs-target
    #: line from ``period_container`` and hides the one-off lifecycle buttons,
    #: which the server refuses on a container anyway.
    is_period_container: bool = False
    #: **True for an old-version, item-grain content work item** - the shape the
    #: projector wrote before ``0039``, one work item per content milestone.
    #: Derived from the row's own provenance columns by
    #: :func:`~meobot.domain.pr.content_work.is_legacy_content_work_item`, so a
    #: page of fifty cards costs no extra query. The client draws a quiet
    #: *"Dữ liệu cũ từ Nội dung"* marker from it; whether the actor may delete
    #: the row is the detail's ``can_delete_legacy``, and the server's refusal
    #: either way.
    is_legacy_content_work: bool = False
    reporting_period_id: uuid.UUID | None = None
    subject_user_id: uuid.UUID | None = None
    period_container: PeriodContainerResponse | None = None
    created_at: datetime
    contributors: list[WorkContributionResponse] = Field(default_factory=list)

    @classmethod
    def from_row(
        cls,
        row: PrWorkItem,
        *,
        now: datetime,
        work_type: PrWorkType | None = None,
        contributions: list[PrWorkContribution] | None = None,
        names: dict[uuid.UUID, str] | None = None,
        content_codes: dict[uuid.UUID, str] | None = None,
        recurring_templates: dict[uuid.UUID, tuple[uuid.UUID, str]] | None = None,
        container: ContainerSummary | None = None,
    ) -> WorkItemResponse:
        from meobot.domain.pr.content_work import is_legacy_content_work_item
        from meobot.domain.pr.work import PrWorkSourceType, PrWorkStatus, is_overdue

        # A stream in an open month is "recording results", whatever rung the
        # one-off ladder would call it; the ladder's words describe a job.
        status_label = work_status_label(row.status)
        if row.is_period_container and row.status in {
            PrWorkStatus.ACCEPTED,
            PrWorkStatus.IN_PROGRESS,
        }:
            status_label = "Đang ghi nhận kết quả"

        return cls(
            id=row.id,
            code=row.code,
            title=row.title,
            description=row.description,
            work_type_id=row.work_type_id,
            work_type_code=work_type.code if work_type else None,
            work_type_name=work_type.name if work_type else None,
            work_type_category=work_type.category.value if work_type else None,
            source_type=row.source_type.value,
            source_label=work_source_label(row.source_type),
            status=row.status.value,
            status_label=status_label,
            priority=row.priority.value,
            priority_label=priority_label(row.priority),
            quantity=row.quantity,
            unit=row.unit.value if row.unit else None,
            unit_label=work_unit_label(row.unit) if row.unit else None,
            due_at=row.due_at,
            execution_at=row.execution_at,
            is_overdue=is_overdue(row.status, row.due_at, now=now),
            created_by_user_id=row.created_by_user_id,
            assigned_by_user_id=row.assigned_by_user_id,
            assigned_at=row.assigned_at,
            accepted_at=row.accepted_at,
            started_at=row.started_at,
            completed_at=row.completed_at,
            approved_at=row.approved_at,
            approved_by_user_id=row.approved_by_user_id,
            cancelled_at=row.cancelled_at,
            cancel_reason=row.cancel_reason,
            channel_id=row.channel_id,
            content_id=row.content_id,
            content_code=(content_codes or {}).get(row.content_id) if row.content_id else None,
            recurring_occurrence_id=row.recurring_occurrence_id,
            recurring_template_id=(
                (recurring_templates or {}).get(row.recurring_occurrence_id, (None, None))[0]
                if row.recurring_occurrence_id
                else None
            ),
            recurring_template_name=(
                (recurring_templates or {}).get(row.recurring_occurrence_id, (None, None))[1]
                if row.recurring_occurrence_id
                else None
            ),
            is_source_derived=row.source_type is not PrWorkSourceType.MANUAL,
            is_period_container=row.is_period_container,
            is_legacy_content_work=is_legacy_content_work_item(row),
            reporting_period_id=row.reporting_period_id,
            subject_user_id=row.subject_user_id,
            period_container=(
                PeriodContainerResponse.from_summary(container, names=names)
                if container is not None
                else None
            ),
            created_at=row.created_at,
            contributors=[
                WorkContributionResponse.from_row(one, names=names) for one in (contributions or [])
            ],
        )


class AdminDeleteEligibilityResponse(BaseModel):
    """**The one administrative delete a detail offers**, and why it may not.

    Present only for somebody who holds ``PR_WORK_CONFIGURE`` reading a row
    that one of the two delete rules covers; everybody else gets ``null`` and
    no control. ``rule`` says which:

    * ``legacy`` - a pre-``0039`` item-grain content row (whatever its
      status), removed through ``DELETE /maintenance/items/{id}``. Its
      eligibility is settled at delete time, so ``deletable`` is true here
      and the route may still refuse;
    * ``terminal`` - a ``CANCELLED`` or ``REJECTED`` ordinary row, removed
      through ``DELETE /maintenance/terminal-items/{id}``, with the server's
      answer from the same counts the delete refuses on. ``blocking`` is what
      the screen lists under *Đang còn:* when the row cannot go.

    A row both rules cover is reported under ``legacy`` - one control per
    panel - and the server accepts either route for it.
    """

    rule: str
    deletable: bool
    reason: str | None = None
    cause: str | None = None
    #: ``results``, ``counted_contributions``, ``quota_allocations``,
    #: ``score_allocations``. Empty when the refusal is not about references.
    blocking: dict[str, int] = Field(default_factory=dict)
    period_code: str | None = None
    message: str | None = None
    #: The row's status as read, so the confirmation names the state being
    #: deleted (*Đã hủy* / *Không được chấp nhận*) instead of guessing.
    previous_status: str | None = None

    @classmethod
    def from_eligibility(
        cls, eligibility: TerminalDeleteEligibility
    ) -> AdminDeleteEligibilityResponse:
        return cls(
            rule="terminal",
            deletable=eligibility.deletable,
            reason=eligibility.reason,
            cause=eligibility.cause,
            blocking=dict(eligibility.blocking),
            period_code=eligibility.period_code,
            message=eligibility.message,
            previous_status=eligibility.previous_status,
        )


class WorkItemDetailResponse(BaseModel):
    """One work item, its evidence, and what this actor may do to it.

    The three ``can_*`` flags are the **server's** answer, from the same checks
    the writes make. ``can_validate`` is false for a contributor whatever
    capability they hold - and calling the route anyway gets the same refusal,
    which is what makes the flag a convenience rather than the rule.
    """

    item: WorkItemResponse
    work_type: WorkTypeResponse
    evidence: list[WorkEvidenceResponse] = Field(default_factory=list)
    #: M3. ``CNT-2026-000042`` when this work was projected from content, so a
    #: detail panel can name the piece and link to it.
    content_code: str | None = None
    can_manage: bool = False
    can_validate: bool = False
    can_execute: bool = False
    #: **The action contract.** One flag per lifecycle write, resolved by
    #: :func:`~meobot.application.pr_work_service.resolve_work_actions` from
    #: the transition table and the same guards the writes apply. The client
    #: draws *Chấp nhận*, *Từ chối*, *Bắt đầu*, *Hoàn thành*, *Xác nhận hoàn
    #: thành*, *Trả lại* and *Hủy* from these and from nothing else - it keeps
    #: no status list of its own. ``REJECTED`` and ``CANCELLED`` are terminal
    #: in the table, so all seven are false on them.
    can_accept: bool = False
    can_reject: bool = False
    can_start: bool = False
    can_complete: bool = False
    can_approve: bool = False
    can_reopen: bool = False
    can_cancel: bool = False
    #: Period-container patch. The results on a stream, oldest first, and the
    #: two result-grain flags: reporting is the subject's or a manager's act,
    #: counting is a validator's who is not the subject.
    results: list[WorkResultResponse] = Field(default_factory=list)
    is_subject: bool = False
    can_report_result: bool = False
    can_validate_results: bool = False
    #: True when this is a legacy content work item **and** the actor holds
    #: ``PR_WORK_CONFIGURE``. The one control it draws is *Xóa công việc*, and
    #: the maintenance route re-checks both halves whatever this said.
    can_delete_legacy: bool = False
    #: **The one administrative delete**, as a flag: true when ``admin_delete``
    #: is present and deletable - a legacy content row, or a ``CANCELLED`` /
    #: ``REJECTED`` ordinary row the server found nothing blocking on, read by
    #: a holder of ``PR_WORK_CONFIGURE``. The maintenance routes re-derive all
    #: of it under a lock whatever this said. Never inferred from ``status``
    #: by a client.
    can_admin_delete: bool = False
    #: The reasoning behind ``can_admin_delete`` - which rule, and when the
    #: answer is no, what the row still holds. ``null`` for everybody who may
    #: not configure and for every row neither rule covers.
    admin_delete: AdminDeleteEligibilityResponse | None = None

    @classmethod
    def from_detail(
        cls,
        detail: WorkItemDetail,
        *,
        now: datetime,
        container: ContainerSummary | None = None,
        results: list[PrWorkResult] | None = None,
        result_names: dict[uuid.UUID, str] | None = None,
        actor_id: uuid.UUID | None = None,
        may_configure: bool = False,
        source_eligibility: dict[uuid.UUID, bool | None] | None = None,
        admin_delete: TerminalDeleteEligibility | None = None,
    ) -> WorkItemDetailResponse:
        from meobot.domain.pr.content_work import is_legacy_content_work_item

        names = {**detail.contributor_names, **(result_names or {})}
        can_validate_results = (
            detail.item.is_period_container and detail.can_validate and not detail.is_subject
        )
        can_delete_legacy = may_configure and is_legacy_content_work_item(detail.item)
        # One delete per panel. The legacy rule wins for a row both cover:
        # its block carries the content resync beside the delete, and the
        # server accepts either route for such a row anyway.
        eligibility: AdminDeleteEligibilityResponse | None
        if can_delete_legacy:
            eligibility = AdminDeleteEligibilityResponse(
                rule="legacy", deletable=True, previous_status=detail.item.status.value
            )
        elif admin_delete is not None:
            eligibility = AdminDeleteEligibilityResponse.from_eligibility(admin_delete)
        else:
            eligibility = None
        return cls(
            item=WorkItemResponse.from_row(
                detail.item,
                now=now,
                work_type=detail.work_type,
                contributions=list(detail.contributions),
                names=names,
                container=container,
                content_codes=(
                    {detail.item.content_id: detail.content_code}
                    if detail.item.content_id and detail.content_code
                    else None
                ),
                recurring_templates=(
                    {detail.item.recurring_occurrence_id: detail.recurring_template}
                    if detail.item.recurring_occurrence_id and detail.recurring_template
                    else None
                ),
            ),
            work_type=WorkTypeResponse.from_row(detail.work_type),
            evidence=[WorkEvidenceResponse.from_row(one) for one in detail.evidence],
            content_code=detail.content_code,
            can_manage=detail.can_manage,
            can_validate=detail.can_validate,
            can_execute=detail.can_execute,
            can_accept=detail.actions.can_accept,
            can_reject=detail.actions.can_reject,
            can_start=detail.actions.can_start,
            can_complete=detail.actions.can_complete,
            can_approve=detail.actions.can_approve,
            can_reopen=detail.actions.can_reopen,
            can_cancel=detail.actions.can_cancel,
            results=[
                WorkResultResponse.from_row(
                    one,
                    names=names,
                    actor_id=actor_id,
                    can_validate=can_validate_results,
                    source_eligible=(source_eligibility or {}).get(one.id),
                )
                for one in (results or [])
            ],
            is_subject=detail.is_subject,
            can_report_result=detail.item.is_period_container
            and (detail.is_subject or detail.can_manage),
            can_validate_results=can_validate_results,
            can_delete_legacy=can_delete_legacy,
            can_admin_delete=eligibility is not None and eligibility.deletable,
            admin_delete=eligibility,
        )


class WorkPageResponse(BaseModel):
    """One page of work."""

    items: list[WorkItemResponse] = Field(default_factory=list)
    total: int = 0
    limit: int = 0
    offset: int = 0

    @classmethod
    def from_page(
        cls,
        page: WorkPage,
        *,
        now: datetime,
        containers: dict[uuid.UUID, ContainerSummary] | None = None,
    ) -> WorkPageResponse:
        types = {one.id: one for one in page.work_types}
        by_item: dict[uuid.UUID, list[PrWorkContribution]] = {}
        for row in page.contributions:
            by_item.setdefault(row.work_item_id, []).append(row)
        return cls(
            items=[
                WorkItemResponse.from_row(
                    row,
                    now=now,
                    work_type=types.get(row.work_type_id),
                    contributions=by_item.get(row.id, []),
                    names=page.contributor_names,
                    content_codes=page.content_codes,
                    recurring_templates=page.recurring_templates,
                    container=(containers or {}).get(row.id),
                )
                for row in page.items
            ],
            total=page.total,
            limit=page.limit,
            offset=page.offset,
        )


class WorkSummaryResponse(BaseModel):
    """The summary strip. **Five period figures and five that ignore periods.**

    ``created``, ``accepted``, ``completed`` and ``approved`` count work items
    whose respective timestamp falls in the period; ``counted_contributions``
    counts one row per person per counted job. The two counted figures are both
    real and different - a three-person shoot is one counted work item and three
    counted contributions - and neither is derived from the other.

    ``open``, ``in_progress``, ``awaiting_validation``, ``proposed`` and
    ``overdue`` describe **now** and are unaffected by the period. That is what
    stops selecting *"Tháng này"* from hiding June's unfinished work.
    """

    period_from: datetime | None = None
    period_to: datetime | None = None
    created: int = 0
    accepted: int = 0
    completed: int = 0
    approved: int = 0
    counted_work_items: int = 0
    counted_contributions: int = 0
    open: int = 0
    in_progress: int = 0
    awaiting_validation: int = 0
    proposed: int = 0
    overdue: int = 0

    @classmethod
    def from_summary(cls, summary: WorkSummary) -> WorkSummaryResponse:
        return cls(
            period_from=summary.period_from,
            period_to=summary.period_to,
            created=summary.created,
            accepted=summary.accepted,
            completed=summary.completed,
            approved=summary.approved,
            counted_work_items=summary.counted_work_items,
            counted_contributions=summary.counted_contributions,
            open=summary.open,
            in_progress=summary.in_progress,
            awaiting_validation=summary.awaiting_validation,
            proposed=summary.proposed,
            overdue=summary.overdue,
        )


# --- Requests ---------------------------------------------------------------


class _WorkBody(_Body):
    """What both creation endpoints share.

    **No ``status`` and no ``source_type``.** Which endpoint was called decides
    both: proposing lands at ``PROPOSED`` and assigning lands at ``ACCEPTED``,
    and every M1 row is ``MANUAL``. A field for either would be a field somebody
    could use to file work as already accepted.
    """

    work_type_id: uuid.UUID
    title: str = Field(min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=4000)
    priority: PrPriority = PrPriority.NORMAL
    quantity: Decimal | None = Field(default=None, gt=0)
    due_at: datetime | None = None
    channel_id: uuid.UUID | None = None


class ProposeWorkRequest(_WorkBody):
    """An employee suggests work. Lands at ``PROPOSED`` - **not** in a KPI.

    ``contributor_user_ids`` may name other people, and the proposer is always
    added to the list whatever it contains: proposing work you had no part in is
    a management act and goes through the other endpoint.
    """

    contributor_user_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)


class AssignWorkRequest(_WorkBody):
    """A manager creates work and puts it on somebody. Lands at ``ACCEPTED``.

    At least one contributor: work assigned to nobody is a note.
    """

    contributor_user_ids: list[uuid.UUID] = Field(min_length=1, max_length=20)


class AssignWorkBatchRequest(_WorkBody):
    """A manager assigns one instruction to several people. **M4A.**

    ``assignment_mode`` is **required whenever more than one person is named**,
    and that is deliberate rather than unfriendly. The two modes are two
    different business facts - one shared job, or one job each - and a default
    would silently pick one of them for a caller who had not thought about it.
    The dangerous direction is recording three people's independent obligations
    as one shared item: one person then completes it for all three, one
    validation counts all three, and nobody can be late on their own work.

    With a single assignee both modes produce byte-identical results, so the
    field is optional there and means nothing.
    """

    contributor_user_ids: list[uuid.UUID] = Field(min_length=1, max_length=20)
    assignment_mode: PrWorkAssignmentMode | None = None

    @model_validator(mode="after")
    def _mode_is_explicit_for_several_people(self) -> AssignWorkBatchRequest:
        if len(set(self.contributor_user_ids)) > 1 and self.assignment_mode is None:
            raise ValueError(
                "Hãy chọn cách giao việc: mỗi người một công việc, hay một công việc chung."
            )
        return self

    @property
    def mode(self) -> PrWorkAssignmentMode:
        """The mode to create with. Either is correct for one assignee."""
        return self.assignment_mode or PrWorkAssignmentMode.SEPARATE_PER_ASSIGNEE


class AssignWorkBatchResponse(BaseModel):
    """What one assignment created.

    A **list**, always, even when it holds one element. The alternative - an
    item for the single case and a list for the batch - is a response shape a
    client has to branch on, and the branch is exactly where somebody reads the
    first of three items and reports "assigned" having shown the person one.
    """

    assignment_mode: str
    items: list[WorkItemDetailResponse]


class BulkValidateRequest(_Body):
    """One validator confirming several finished jobs. **M4A.**

    ``work_item_ids`` is the frozen target set, resolved from the panel's
    selection before the confirmation dialog opens.
    """

    work_item_ids: list[uuid.UUID] = Field(min_length=1, max_length=BULK_VALIDATION_MAX_ITEMS)
    note: str | None = Field(default=None, max_length=4000)


class BulkValidationCandidateResponse(BaseModel):
    """One row of a preflight: what it is, and why it may not go."""

    work_item_id: uuid.UUID
    code: str | None
    title: str | None
    #: ``null`` when the row is fine. Otherwise the machine reason - the client
    #: turns it into a sentence, so a new reason never ships as a raw token.
    reason: str | None
    reason_label: str | None
    current_status: str | None

    @classmethod
    def of(cls, candidate: BulkValidationCandidate) -> BulkValidationCandidateResponse:
        return cls(
            work_item_id=candidate.work_item_id,
            code=candidate.code,
            title=candidate.title,
            reason=candidate.reason,
            reason_label=(
                bulk_validation_reason_label(candidate.reason)
                if candidate.reason is not None
                else None
            ),
            current_status=(
                candidate.current_status.value if candidate.current_status is not None else None
            ),
        )


class BulkValidationPreflightResponse(BaseModel):
    """What a batch would do, without doing any of it."""

    candidates: list[BulkValidationCandidateResponse]
    validatable_count: int
    blocked_count: int
    requested: int
    duplicates_removed: int
    max_items: int = BULK_VALIDATION_MAX_ITEMS

    @classmethod
    def of(cls, preflight: BulkValidationPreflight) -> BulkValidationPreflightResponse:
        return cls(
            candidates=[BulkValidationCandidateResponse.of(one) for one in preflight.candidates],
            validatable_count=len(preflight.validatable),
            blocked_count=len(preflight.blocked),
            requested=preflight.requested,
            duplicates_removed=preflight.duplicates_removed,
        )


class BulkValidationOutcomeResponse(BaseModel):
    """What a successful batch did. Only ever returned when all of it worked."""

    batch_id: uuid.UUID
    validated_count: int
    #: Larger than ``validated_count`` whenever a shared job had several
    #: contributors: the batch validated *n* jobs and counted *m* people's work.
    counted_contributions: int
    requested: int
    duplicates_removed: int
    validated: list[BulkValidationCandidateResponse]

    @classmethod
    def of(cls, outcome: BulkValidationOutcome) -> BulkValidationOutcomeResponse:
        return cls(
            batch_id=outcome.batch_id,
            validated_count=len(outcome.validated),
            counted_contributions=outcome.counted_contributions,
            requested=outcome.requested,
            duplicates_removed=outcome.duplicates_removed,
            validated=[BulkValidationCandidateResponse.of(one) for one in outcome.validated],
        )


class AssigneeReadinessResponse(BaseModel):
    """Whether one person's counted work of this kind would meet a KPI quota."""

    user_id: uuid.UUID
    full_name: str
    has_quota: bool


class WorkReadinessResponse(BaseModel):
    """The two configuration facts the creation screen states out loud. **M4A.**

    Carries **no verdict**, because there is nothing to be ready for: every
    combination of these flags creates perfectly valid work. Both are sentences
    a screen shows, never a reason a write is refused.
    """

    work_type_id: uuid.UUID
    work_type_name: str
    has_scoring_rule: bool
    period_id: uuid.UUID | None
    period_label: str | None
    assignees: list[AssigneeReadinessResponse]
    assignees_without_quota: int

    @classmethod
    def of(cls, readiness: WorkReadiness) -> WorkReadinessResponse:
        return cls(
            work_type_id=readiness.work_type_id,
            work_type_name=readiness.work_type_name,
            has_scoring_rule=readiness.has_scoring_rule,
            period_id=readiness.period_id,
            period_label=readiness.period_label,
            assignees=[
                AssigneeReadinessResponse(
                    user_id=one.user_id, full_name=one.full_name, has_quota=one.has_quota
                )
                for one in readiness.assignees
            ],
            assignees_without_quota=len(readiness.assignees_without_quota),
        )


class WorkNoteRequest(_Body):
    """A note or a reason attached to a lifecycle action."""

    note: str | None = Field(default=None, max_length=4000)


class AddContributorRequest(_Body):
    """Put somebody on a job.

    ``credit_weight`` is a manager's field - the endpoint requires
    ``PR_WORK_MANAGE`` - and the column refuses anything above a whole unit
    whoever writes it.
    """

    user_id: uuid.UUID
    contribution_role: PrWorkContributionRole = PrWorkContributionRole.CONTRIBUTOR
    credit_weight: Decimal | None = Field(default=None, gt=0, le=1)


class ChangeDeadlineRequest(_Body):
    """Move a deadline. ``null`` removes it, which is not the same as leaving it."""

    due_at: datetime | None = None
    reason: str | None = Field(default=None, max_length=4000)


class ChangePriorityRequest(_Body):
    priority: PrPriority


class AddEvidenceRequest(_Body):
    """Evidence, in either of two shapes. MeoBot stores no files.

    **One text** - ``text`` - is what the screen sends: a description, a link,
    several lines, any mix, and no URL required. The **legacy** shape is a
    ``label`` and a ``location`` (a URL or a path), kept for clients that still
    send it. A body must be one or the other; an empty body, or a text beside a
    label, is refused so the row's meaning is never a guess.
    """

    text: str | None = Field(default=None, min_length=1, max_length=4000)
    label: str | None = Field(default=None, min_length=1, max_length=200)
    location: str | None = Field(default=None, min_length=1, max_length=2000)
    note: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def _one_shape(self) -> AddEvidenceRequest:
        has_text = self.text is not None and self.text.strip() != ""
        has_link = self.label is not None or self.location is not None
        if has_text and has_link:
            raise ValueError("send either text or a label and location, not both")
        if has_text:
            return self
        if self.label is None or self.location is None:
            raise ValueError("evidence needs text, or a label and a location")
        return self


class ReportResultRequest(_Body):
    """One result declared into a stream. **The generic report form.**

    ``quantity`` defaults to one - a script, a customer, an order. ``label``
    and ``link`` are free text a person attaches; nothing here is
    work-type-specific. Naming ``work_type_id`` (and, for a manager,
    ``subject_user_id``) reports into the month's stream and opens it on
    first use; posting to ``/{work_item_id}/results`` names the stream directly.

    **No target is consulted.** Reporting past a KPI, or with no KPI at all, is
    the same request as reporting the first unit.
    """

    quantity: Decimal | None = Field(default=None, gt=0)
    label: str | None = Field(default=None, max_length=200)
    link: str | None = Field(default=None, max_length=2000)
    note: str | None = Field(default=None, max_length=4000)
    occurred_at: datetime | None = None
    work_type_id: uuid.UUID | None = None
    subject_user_id: uuid.UUID | None = None
    period_id: uuid.UUID | None = None


class ValidateResultsRequest(_Body):
    """Count pending results. Every pending one when ``result_ids`` is omitted."""

    result_ids: list[uuid.UUID] | None = Field(default=None, max_length=500)
    note: str | None = Field(default=None, max_length=4000)


class ExcludeResultRequest(_Body):
    """*Từ chối / Không ghi nhận*. A reason is required, and whitespace is not one."""

    reason: str = Field(min_length=1, max_length=4000)


class ReconsiderResultRequest(_Body):
    """*Xem xét lại*: release a validator's rejection back to ``PENDING``."""

    note: str | None = Field(default=None, max_length=4000)


__all__: list[str] = [
    "AddContributorRequest",
    "AddEvidenceRequest",
    "AdminDeleteEligibilityResponse",
    "AssignWorkRequest",
    "ChangeDeadlineRequest",
    "ChangePriorityRequest",
    "CreateWorkTypeRequest",
    "ExcludeResultRequest",
    "PeriodContainerResponse",
    "ProposeWorkRequest",
    "ReconsiderResultRequest",
    "ReportResultRequest",
    "UpdateWorkTypeRequest",
    "ValidateResultsRequest",
    "WorkContributionResponse",
    "WorkEvidenceResponse",
    "WorkHistoryResponse",
    "WorkItemDetailResponse",
    "WorkItemResponse",
    "WorkNoteRequest",
    "WorkPageResponse",
    "WorkResultResponse",
    "WorkSummaryResponse",
    "WorkTypeResponse",
]
