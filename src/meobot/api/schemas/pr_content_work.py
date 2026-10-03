"""What the Content → Work projection API sends and accepts. M3.

Its own module beside the M1 and M2 work schemas, because the readers are
different: this is an administrator configuring a mapping and reading a
projection report, not an employee looking at a board.

Three rules run through every model here
-----------------------------------------

**No eligibility, anywhere.** No quota, no cap, no allocation, no
``quota_status``, and no ``score``. M3 answers *"has the content workflow
produced trustworthy ``COUNTED`` work"*; what that work is worth against a cap is
M2's, and a field here echoing any of it would invite a client to read the wrong
authority.

**No milestone is configurable.** The request bodies carry a work type and
nothing else that decides anything. Which content edge counts as work, who the
contributor is and when independent validation is required are domain rules -
exposing them as dropdowns would be exposing the anti-gaming boundary as a
setting.

**Outcomes are reported, never resolved.** ``NO_MAPPING``,
``UNRESOLVED_CONTRIBUTOR`` and ``BLOCKED_BY_PERIOD`` come back as themselves
rather than being smoothed into a success count. A reconcile report that showed
only what worked would be silence dressed as success.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from meobot.application.pr_content_work_projector import (
    ContentProjectionReport,
    ProjectionResult,
    ReconcileReport,
)
from meobot.db.models.pr_content_work import PrContentWorkProjection, PrContentWorkRule
from meobot.db.models.pr_work import PrWorkType
from meobot.domain.pr.content_work import PrContentWorkKind
from meobot.domain.pr.models import PrContentType


class _Body(BaseModel):
    """Request bodies refuse unknown fields.

    ``extra="forbid"`` rather than ``ignore``, and it is load-bearing here: a
    body carrying ``milestone``, ``contributor_user_id`` or ``counted_at`` is
    **refused** rather than quietly dropped. Those are the three things a client
    must never be able to suggest, and a silent drop would look to the caller
    exactly like being obeyed.
    """

    model_config = ConfigDict(extra="forbid")


class ContentWorkRuleResponse(BaseModel):
    """One mapping: which work type a kind of content milestone counts as."""

    id: uuid.UUID
    #: ``CONTENT_CREATION`` | ``PRODUCTION`` | ``PUBLICATION``.
    contribution_kind: str
    #: The classification this rule is for. ``null`` is **the default for the
    #: kind**, and a client should say so rather than printing "chưa phân loại".
    content_type: str | None = None
    work_type_id: uuid.UUID
    work_type_code: str | None = None
    work_type_name: str | None = None
    is_active: bool
    note: str | None = None
    created_at: datetime
    #: Whether the projector wrote this rule - it met a content type nobody had
    #: mapped and bound it to a work type it created - rather than a person.
    #: An ordinary rule in every other respect: it may be remapped or
    #: deactivated here like any other, and the flag records how it began.
    auto_provisioned: bool = False

    @classmethod
    def from_row(
        cls, row: PrContentWorkRule, *, work_types: dict[uuid.UUID, PrWorkType] | None = None
    ) -> ContentWorkRuleResponse:
        work_type = (work_types or {}).get(row.work_type_id)
        return cls(
            id=row.id,
            contribution_kind=row.contribution_kind.value,
            content_type=row.content_type.value if row.content_type else None,
            work_type_id=row.work_type_id,
            work_type_code=work_type.code if work_type else None,
            work_type_name=work_type.name if work_type else None,
            is_active=row.is_active,
            note=row.note,
            created_at=row.created_at,
            auto_provisioned=row.auto_provisioned,
        )


class UpsertContentWorkRuleRequest(_Body):
    """Set the mapping for one case. ``PR_WORK_CONFIGURE``. **Idempotent.**

    Upsert rather than create, because *"short-video scripts count as
    ``SHORT_VIDEO_SCRIPT``"* is a statement about the world rather than a row
    somebody owns: saying it twice leaves one rule, and saying something
    different replaces the first answer rather than producing two the projector
    would have to choose between.

    ``content_type`` omitted means **the default for the kind**. Exact beats
    default at resolution time, so a department can say one broad thing and
    refine it later without enumerating everything else first.
    """

    contribution_kind: PrContentWorkKind
    content_type: PrContentType | None = None
    work_type_id: uuid.UUID
    is_active: bool = True
    note: str | None = Field(default=None, max_length=2000)


class ProjectionResultResponse(BaseModel):
    """What one run concluded about one semantic piece of work."""

    #: Which kind of job this is about.
    contribution_kind: str
    #: ``PROJECTED`` | ``UNCHANGED`` | ``PENDING_VALIDATION`` | ``REVERSED`` |
    #: ``NOT_QUALIFIED`` | ``NO_MAPPING`` | ``UNRESOLVED_CONTRIBUTOR`` |
    #: ``BLOCKED_BY_PERIOD``.
    #:
    #: **Not a quota status.** ``UNRESOLVED_CONTRIBUTOR`` says the projector
    #: cannot name whose work this was, which has nothing to do with whether a
    #: quota exists.
    outcome: str
    source_key: str | None = None
    work_item_id: uuid.UUID | None = None
    #: One sentence of English for an operator. Never an exception message.
    detail: str | None = None

    @classmethod
    def from_result(cls, result: ProjectionResult) -> ProjectionResultResponse:
        return cls(
            contribution_kind=result.kind.value,
            outcome=result.outcome.value,
            source_key=result.source_key,
            work_item_id=result.work_item_id,
            detail=result.detail,
        )


class ContentProjectionReportResponse(BaseModel):
    """Everything one content item's projection concluded."""

    content_id: uuid.UUID
    content_code: str
    #: The least settled of the results, so a piece whose script projected and
    #: whose production has no mapping reads as needing attention.
    outcome: str
    results: list[ProjectionResultResponse] = Field(default_factory=list)

    @classmethod
    def from_report(cls, report: ContentProjectionReport) -> ContentProjectionReportResponse:
        return cls(
            content_id=report.content_id,
            content_code=report.content_code,
            outcome=report.worst.value,
            results=[ProjectionResultResponse.from_result(one) for one in report.results],
        )


class ReconcileContentWorkRequest(_Body):
    """Ask for a **bounded** catch-up over content that already happened.

    Not a backfill, and the shape says so: either a list of content ids
    somebody chose, or a bounded default over content with a milestone in a
    currently-open reporting period. There is no "everything" and no date range
    reaching into closed months.

    ``dry_run`` runs every read and every decision and writes nothing, so an
    operator can see what a catch-up would do before doing it.
    """

    content_ids: list[uuid.UUID] | None = Field(default=None, max_length=200)
    limit: int = Field(default=50, ge=1, le=200)
    dry_run: bool = False


class ReconcileContentWorkResponse(BaseModel):
    """What one catch-up did, counted by outcome.

    ``counts`` is keyed by outcome and **includes the unhappy ones**, which is
    the point: ``no_mapping`` and ``unresolved_contributor`` are the operator's
    next actions, and a response that reported only successes would hide them.
    """

    content_items: int = 0
    dry_run: bool = False
    counts: dict[str, int] = Field(default_factory=dict)
    reports: list[ContentProjectionReportResponse] = Field(default_factory=list)

    @classmethod
    def from_report(cls, report: ReconcileReport) -> ReconcileContentWorkResponse:
        return cls(
            content_items=report.content_items,
            dry_run=report.dry_run,
            counts=report.counts,
            reports=[ContentProjectionReportResponse.from_report(one) for one in report.reports],
        )


class ContentWorkProjectionStatusResponse(BaseModel):
    """One content item's standing with the projector, for diagnostics."""

    content_id: uuid.UUID
    status: str
    requested_at: datetime
    claimed_at: datetime | None = None
    settled_at: datetime | None = None
    attempts: int = 0
    last_outcome: str | None = None
    #: An exception **class name** when a run failed unexpectedly, never a
    #: message: an error string is an implementation detail, and putting one on
    #: an admin screen is putting a stack trace on an admin screen.
    last_error_code: str | None = None

    @classmethod
    def from_row(cls, row: PrContentWorkProjection) -> ContentWorkProjectionStatusResponse:
        return cls(
            content_id=row.content_id,
            status=row.status.value,
            requested_at=row.requested_at,
            claimed_at=row.claimed_at,
            settled_at=row.settled_at,
            attempts=row.attempts,
            last_outcome=row.last_outcome.value if row.last_outcome else None,
            last_error_code=row.last_error_code,
        )


__all__: list[str] = [
    "ContentProjectionReportResponse",
    "ContentWorkProjectionStatusResponse",
    "ContentWorkRuleResponse",
    "ProjectionResultResponse",
    "ReconcileContentWorkRequest",
    "ReconcileContentWorkResponse",
    "UpsertContentWorkRuleRequest",
]
