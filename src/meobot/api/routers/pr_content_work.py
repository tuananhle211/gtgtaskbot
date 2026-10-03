"""``/api/pr/work/content`` - the Content → Work projection surface. M3.

Small, and deliberately so. Almost all of M3 happens without anybody calling
anything: a content transition writes a request row, a beat task sweeps it, and
the work appears. What is left for HTTP is the two things a person genuinely
decides - **what maps to what**, and **catch up on this content now** - plus a
diagnostic read.

There is **no endpoint that projects one milestone**, no endpoint that sets a
contributor, and no endpoint that supplies a timestamp. Those are the three
things a client must never be able to suggest, and the way to keep that true is
for no route to accept them: ``count_source_work`` and ``reverse_source_work``
are internal methods on the work service that no router reaches, and a test
asserts it.

Authorization is the services', not this layer's
-------------------------------------------------

Every route calls a service method that requires ``PR_WORK_CONFIGURE`` - the
same capability that edits the work taxonomy and approves a KPI plan, because
this is the same kind of decision. ``PR_WORK_MANAGE`` opens nothing here: a
Trưởng nhóm who assigns work does not thereby decide what the department's
content counts as.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query, status
from sqlalchemy import select

from meobot.api.deps import CurrentActorDep, PrServicesDep, RequestIdDep
from meobot.api.schemas.pr_content_work import (
    ContentProjectionReportResponse,
    ContentWorkProjectionStatusResponse,
    ContentWorkRuleResponse,
    ReconcileContentWorkRequest,
    ReconcileContentWorkResponse,
    UpsertContentWorkRuleRequest,
)
from meobot.db.models.pr_content_work import PrContentWorkProjection
from meobot.domain.pr.policy import PrCapability

router = APIRouter(prefix="/api/pr/work/content", tags=["pr-content-work"])

_WRITE_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"description": "No usable session."},
    403: {"description": "Refused - PR_WORK_CONFIGURE."},
    404: {"description": "No such work type or content item."},
    422: {"description": "The request itself is not valid."},
}
_READ_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"description": "No usable session."},
    403: {"description": "Refused - PR_WORK_CONFIGURE."},
}


# --- The mapping ------------------------------------------------------------


@router.get(
    "/rules",
    response_model=list[ContentWorkRuleResponse],
    summary="Which work type each content milestone counts as",
    responses=_READ_RESPONSES,
)
async def list_rules(
    actor: CurrentActorDep, services: PrServicesDep
) -> list[ContentWorkRuleResponse]:
    """``PR_WORK_CONFIGURE``. Every rule, active or not.

    Inactive ones included: a deactivated rule is part of the picture somebody
    editing the mapping needs, and hiding it would make *"why is nothing being
    projected"* harder to answer rather than easier.
    """
    rules = await services.content_work_rules.list_rules(actor=actor)
    work_types = await services.content_work_rules.work_types_for(rules)
    return [ContentWorkRuleResponse.from_row(row, work_types=work_types) for row in rules]


@router.put(
    "/rules",
    response_model=ContentWorkRuleResponse,
    summary="Set the mapping for one content kind",
    responses=_WRITE_RESPONSES,
)
async def upsert_rule(
    body: UpsertContentWorkRuleRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> ContentWorkRuleResponse:
    """``PR_WORK_CONFIGURE``. **Idempotent**, which is why it is ``PUT``.

    The body carries a work type and nothing that decides anything else. There
    is no milestone field, no contributor field and no timestamp: those are
    domain rules, and a settings screen that could change them would be a
    settings screen that turns the anti-gaming boundary off.

    Changing a rule affects **what happens next** and rewrites nothing: every
    work item records the type it was filed as, so yesterday's counted work
    keeps saying what it always said.
    """
    rule = await services.content_work_rules.upsert_rule(
        actor=actor,
        request_id=request_id,
        contribution_kind=body.contribution_kind,
        content_type=body.content_type,
        work_type_id=body.work_type_id,
        is_active=body.is_active,
        note=body.note,
    )
    work_types = await services.content_work_rules.work_types_for([rule])
    return ContentWorkRuleResponse.from_row(rule, work_types=work_types)


# --- Catch-up ---------------------------------------------------------------


@router.post(
    "/reconcile",
    response_model=ReconcileContentWorkResponse,
    summary="Catch up on content that already happened",
    responses=_WRITE_RESPONSES,
)
async def reconcile(
    body: ReconcileContentWorkRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> ReconcileContentWorkResponse:
    """``PR_WORK_CONFIGURE``. **Bounded, explicit and idempotent.**

    The thing the migration deliberately did not do. Projecting years of past
    content would mean deciding, with nobody watching, who wrote a script two
    Augusts ago and whether the month it lands in may still be written to - and
    both answers are sometimes *"we cannot know"*.

    So it is a request somebody makes and watches. Without ``content_ids`` the
    default is content with a live milestone in a currently-open reporting
    period: closed months are untouched **by construction** rather than by a
    filter somebody could forget.

    ``dry_run`` writes nothing and reports what would happen.
    """
    return ReconcileContentWorkResponse.from_report(
        await services.content_work.reconcile(
            actor=actor,
            request_id=request_id,
            content_ids=body.content_ids,
            limit=body.limit,
            dry_run=body.dry_run,
        )
    )


@router.post(
    "/{content_id}/project",
    response_model=ContentProjectionReportResponse,
    status_code=status.HTTP_200_OK,
    summary="Converge one content item's work with its source",
    responses=_WRITE_RESPONSES,
)
async def project_one(
    content_id: uuid.UUID,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
    dry_run: Annotated[bool, Query()] = False,
) -> ContentProjectionReportResponse:
    """``PR_WORK_CONFIGURE``. One content item, now rather than at the next sweep.

    The same convergence the worker runs - *"catch up on this"* and *"handle
    this event"* are one operation, so there is no second implementation to
    keep in step. Safe to call repeatedly: a run that changes nothing reports
    ``UNCHANGED`` and writes nothing.
    """
    # Through ``reconcile`` rather than straight to ``project_content``, because
    # ``reconcile`` is where the capability check and the audit row live -
    # calling the inner method here would be a route that projects without
    # either, which is exactly the shortcut this module exists not to have.
    report = await services.content_work.reconcile(
        actor=actor,
        request_id=request_id,
        content_ids=[content_id],
        dry_run=dry_run,
    )
    return ContentProjectionReportResponse.from_report(report.reports[0])


# --- Diagnostics ------------------------------------------------------------


@router.get(
    "/projections",
    response_model=list[ContentWorkProjectionStatusResponse],
    summary="What the projector is waiting on, and what failed",
    responses=_READ_RESPONSES,
)
async def list_projections(
    actor: CurrentActorDep,
    services: PrServicesDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[ContentWorkProjectionStatusResponse]:
    """``PR_WORK_CONFIGURE``. The queue, newest request first.

    The operational half of *"is automatic work recording working"*: a growing
    ``PENDING`` list means the worker is down, a ``FAILED`` row names the
    exception class, and a settled row's ``last_outcome`` says whether the piece
    reached an answer or is waiting on a mapping somebody has not written.
    """
    await services.capabilities.require(actor, PrCapability.PR_WORK_CONFIGURE)
    statement = (
        select(PrContentWorkProjection)
        .order_by(PrContentWorkProjection.requested_at.desc())
        .limit(limit)
    )
    rows = (await services.session.execute(statement)).scalars().all()
    return [ContentWorkProjectionStatusResponse.from_row(row) for row in rows]
