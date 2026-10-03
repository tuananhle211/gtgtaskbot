"""``/api/pr/work/recurring`` - recurring templates over HTTP. M4B.

Its own router beside M1's for the reason M2's and M3's are separate: templates
are configuration with their own screen, and mixing them into a router that is
already the ledger's would make one module's routes harder to reason about
without making either shorter.

**Mounted before M1's work router.** It owns the literal
``/api/pr/work/recurring`` prefix and M1 owns ``GET /api/pr/work/{work_item_id}``;
FastAPI matches in registration order, so declared later ``recurring`` would be
parsed as a work item id and answered with a 422. The same ordering M2's
``/periods`` and M3's ``/content`` needed.

Explicit actions, never one big PATCH
--------------------------------------

``/activate``, ``/pause``, ``/resume``, ``/end`` - four endpoints calling four
service methods, and no ``PATCH`` anywhere that takes a status. This is not
style: activation is the moment a manager's standing authorization for a routine
begins and every job it later generates is filed under their name, so it must be
an act somebody performed rather than a field somebody set. The bodies use
``extra="forbid"``, so one carrying ``status`` or a cursor is refused rather
than ignored.

**There is no route that generates work.** Not a "run now", not a "backfill".
Generation happens on the beat sweep, walking the cursor forward under the rules
that make it safe - the activation boundary, the pause window, the closed-period
refusal, the catch-up bound. An HTTP endpoint that produced work on demand would
be a second implementation of those rules and the one people reached for when
the first one said no.

Authorization is the service's
-------------------------------

No route here reads a role or compares a user id. Every one calls a method that
requires ``PR_WORK_MANAGE``, so a direct API call gets exactly the refusal a
hidden button would have prevented.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query, Response, status

from meobot.api.deps import CurrentActorDep, PrServicesDep, RequestIdDep
from meobot.api.schemas.pr_work_recurring import (
    RecurringOccurrenceListResponse,
    RecurringOccurrenceResponse,
    RecurringTemplateListResponse,
    RecurringTemplateRequest,
    RecurringTemplateResponse,
    SchedulePreviewRequest,
    SchedulePreviewResponse,
)
from meobot.application.pr_work_recurring_service import RecurringTemplateCommand
from meobot.domain.pr.policy import PrCapability
from meobot.domain.pr.recurring import PrRecurringTemplateStatus

router = APIRouter(prefix="/api/pr/work/recurring", tags=["pr-work-recurring"])

_NOT_FOUND: dict[int | str, dict[str, Any]] = {404: {"description": "No such recurring template."}}
_WRITE_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"description": "No usable session."},
    403: {"description": "Refused by the work services - ``PR_WORK_MANAGE``."},
    409: {"description": "Refused by a template lifecycle rule."},
    422: {"description": "The request itself is not valid."},
    **_NOT_FOUND,
}


def _command(body: RecurringTemplateRequest) -> RecurringTemplateCommand:
    """A request body as the application layer's own command.

    Carries no status, no revision and no cursor: this layer decides none of
    those, so there is no body anywhere that can file a routine as running or
    claim that six weeks of occurrences have been dealt with.
    """
    return RecurringTemplateCommand(
        name=body.name,
        work_type_id=body.work_type_id,
        assignment_mode=body.assignment_mode,
        frequency=body.frequency,
        run_time=body.run_time,
        start_date=body.start_date,
        contributor_user_ids=tuple(body.contributor_user_ids),
        weekdays=tuple(body.weekdays),
        day_of_month=body.day_of_month,
        quantity=body.quantity,
        priority=body.priority,
        due_after_hours=body.due_after_hours,
        end_date=body.end_date,
        description=body.description,
        accumulate_by_period=body.accumulate_by_period,
    )


async def _detail(
    services: PrServicesDep, actor: CurrentActorDep, template_id: uuid.UUID
) -> RecurringTemplateResponse:
    """Re-read one template after a write.

    Every action returns the whole refreshed routine rather than the field it
    changed, because what the caller wants to know is *what this routine will do
    now* - including its recomputed preview, which a client that had to ask
    separately would draw one frame of the old answer for.
    """
    return RecurringTemplateResponse.from_detail(
        await services.work_recurring.template_detail(actor=actor, template_id=template_id)
    )


# --- Reading -----------------------------------------------------------------


@router.get(
    "",
    response_model=RecurringTemplateListResponse,
    summary="Every recurring template",
    responses=_WRITE_RESPONSES,
)
async def list_templates(
    actor: CurrentActorDep,
    services: PrServicesDep,
    status_filter: Annotated[PrRecurringTemplateStatus | None, Query(alias="status")] = None,
) -> RecurringTemplateListResponse:
    """The department's standing instructions. ``PR_WORK_MANAGE``.

    Reading them is the same act as writing them, which is why an employee does
    not reach this route: what an employee has to do is the *work* a template
    generates, and that is on the ordinary ledger with everything else.
    """
    details = await services.work_recurring.list_templates(
        actor=actor, statuses=(status_filter,) if status_filter is not None else None
    )
    return RecurringTemplateListResponse(
        items=[RecurringTemplateResponse.from_detail(one) for one in details]
    )


@router.post(
    "/preview",
    response_model=SchedulePreviewResponse,
    summary="What this schedule would do",
    responses=_WRITE_RESPONSES,
)
async def preview_schedule(
    body: SchedulePreviewRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
) -> SchedulePreviewResponse:
    """The next few firings, before anything is written. ``PR_WORK_MANAGE``. Writes nothing.

    ``POST`` because the body is the whole form, not because it changes
    anything. It exists so a manager sees *"09:00 mỗi thứ Hai, thứ Tư"* and four
    real dates before committing to a routine that will keep creating work until
    somebody stops it - and it is computed on the server, from the same schedule
    object the generator fires from, so the preview and the behaviour cannot
    disagree.

    Registered **before** ``/{template_id}`` - FastAPI matches in declaration
    order, and a dynamic path declared first would take ``preview`` as an id.
    """
    await services.capabilities.require(actor, PrCapability.PR_WORK_MANAGE)
    label, firings = services.work_recurring.preview(_command(body))
    return SchedulePreviewResponse(schedule_label=label, next_occurrences=list(firings))


@router.get(
    "/{template_id}",
    response_model=RecurringTemplateResponse,
    summary="One recurring template",
    responses=_WRITE_RESPONSES,
)
async def get_template(
    template_id: uuid.UUID, actor: CurrentActorDep, services: PrServicesDep
) -> RecurringTemplateResponse:
    """One routine, with its schedule sentence and next firings."""
    return await _detail(services, actor, template_id)


@router.get(
    "/{template_id}/occurrences",
    response_model=RecurringOccurrenceListResponse,
    summary="One template's scheduler history",
    responses=_WRITE_RESPONSES,
)
async def list_occurrences(
    template_id: uuid.UUID,
    actor: CurrentActorDep,
    services: PrServicesDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> RecurringOccurrenceListResponse:
    """Every scheduled firing and what became of it. ``PR_WORK_MANAGE``.

    **The screen that answers "why is there no work for Tuesday."** It is the
    only place the scheduler's own states surface at all, and that is deliberate:
    a manager configuring a routine never sees them, and a manager investigating
    one needs nothing else.
    """
    rows = await services.work_recurring.occurrences(
        actor=actor, template_id=template_id, limit=limit
    )
    return RecurringOccurrenceListResponse(
        items=[RecurringOccurrenceResponse.from_row(row) for row in rows]
    )


# --- Writing -----------------------------------------------------------------


@router.post(
    "",
    response_model=RecurringTemplateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a recurring template",
    responses=_WRITE_RESPONSES,
)
async def create_template(
    body: RecurringTemplateRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> RecurringTemplateResponse:
    """Write a routine. Lands at ``Nháp``. ``PR_WORK_MANAGE``.

    **Generates nothing yet.** A draft is a description; activation is what turns
    it into a standing instruction, and the extra click is the point - the
    schedule preview is there to be read first.
    """
    template = await services.work_recurring.create_template(
        actor=actor, request_id=request_id, command=_command(body)
    )
    return await _detail(services, actor, template.id)


@router.put(
    "/{template_id}",
    response_model=RecurringTemplateResponse,
    summary="Edit a recurring template",
    responses=_WRITE_RESPONSES,
)
async def update_template(
    template_id: uuid.UUID,
    body: RecurringTemplateRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> RecurringTemplateResponse:
    """Rewrite a routine and bump its revision. ``PR_WORK_MANAGE``.

    Allowed while it is running, because a routine whose quantity went from
    eighty comments to a hundred is normal operations. **Work already generated
    is never rewritten** - a job created last Tuesday records what was asked for
    last Tuesday - and the occurrence ledger keeps the revision number that
    produced each firing, so a sweep cannot straddle the edit.
    """
    await services.work_recurring.update_template(
        actor=actor, request_id=request_id, template_id=template_id, command=_command(body)
    )
    return await _detail(services, actor, template_id)


@router.delete(
    "/{template_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an unused draft",
    responses=_WRITE_RESPONSES,
)
async def delete_template(
    template_id: uuid.UUID,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> Response:
    """Remove a draft the scheduler never reached. ``PR_WORK_MANAGE``.

    Refused for anything that has run, and refused the moment the occurrence
    ledger has any row at all - including one that failed or was declined,
    because both are answers somebody may need. A routine that has run is
    **ended**, which keeps its history and stops it generating.
    """
    await services.work_recurring.delete_template(
        actor=actor, request_id=request_id, template_id=template_id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{template_id}/activate",
    response_model=RecurringTemplateResponse,
    summary="Start a recurring template",
    responses=_WRITE_RESPONSES,
)
async def activate_template(
    template_id: uuid.UUID,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> RecurringTemplateResponse:
    """Start the routine. ``PR_WORK_MANAGE``. **This is the authorization.**

    Every job the template generates from now on is created by and assigned by
    the caller, and lands at ``Được giao`` - *the assignment is the
    authorization*, exactly as it is for a manager assigning one job by hand.

    It is **not** validation. Nothing here makes any work count; the independent
    approval M1 requires is still required, by somebody who did not do the work.

    No history is generated. Activation sets the cursor to the later of the start
    date and this moment, so a routine written six weeks ago does not open with
    six weeks of backdated work.
    """
    await services.work_recurring.activate(
        actor=actor, request_id=request_id, template_id=template_id
    )
    return await _detail(services, actor, template_id)


@router.post(
    "/{template_id}/pause",
    response_model=RecurringTemplateResponse,
    summary="Pause a recurring template",
    responses=_WRITE_RESPONSES,
)
async def pause_template(
    template_id: uuid.UUID,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> RecurringTemplateResponse:
    """Stop generating, without ending the routine. ``PR_WORK_MANAGE``.

    Work already generated is untouched and still has to be done: pausing the
    instruction is not cancelling the obligations it already created.
    """
    await services.work_recurring.pause(actor=actor, request_id=request_id, template_id=template_id)
    return await _detail(services, actor, template_id)


@router.post(
    "/{template_id}/resume",
    response_model=RecurringTemplateResponse,
    summary="Resume a paused recurring template",
    responses=_WRITE_RESPONSES,
)
async def resume_template(
    template_id: uuid.UUID,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> RecurringTemplateResponse:
    """Start generating again. ``PR_WORK_MANAGE``. **The pause is not backfilled.**

    The cursor moves forward to now, so the suspended interval produces no work
    at all. That is what distinguishes a pause from an outage: nobody decided
    anything during an outage, the cursor stayed where it was, and the missed
    firings are caught up on the next sweep.
    """
    await services.work_recurring.resume(
        actor=actor, request_id=request_id, template_id=template_id
    )
    return await _detail(services, actor, template_id)


@router.post(
    "/{template_id}/end",
    response_model=RecurringTemplateResponse,
    summary="End a recurring template",
    responses=_WRITE_RESPONSES,
)
async def end_template(
    template_id: uuid.UUID,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> RecurringTemplateResponse:
    """Retire the routine. ``PR_WORK_MANAGE``. Terminal.

    Kept rather than deleted, so everything it generated can still be explained.
    A routine that is over is copied, not restarted.
    """
    await services.work_recurring.end(actor=actor, request_id=request_id, template_id=template_id)
    return await _detail(services, actor, template_id)
