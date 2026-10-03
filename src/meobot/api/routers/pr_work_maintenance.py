"""Work maintenance: content sync, rebuild, removals, type deletion.

**Every route here is ``PR_WORK_CONFIGURE``**, checked by the service and not
by anything in this file. ADMIN and OWNER hold it; TEAM_LEAD and EMPLOYEE do
not, and a request they forge is refused with a 403 by the same line that
refuses it for everyone else. No role name is read anywhere on this path.

Explicit routes rather than one endpoint that takes an operation name: what
each of these does to accounting is different enough that each deserves its own
summary, its own audit action and its own confirmation on the screen.

Mounted under ``/api/pr/work/maintenance`` and **before** M1's work router,
which owns ``GET /api/pr/work/{work_item_id}``: FastAPI matches in registration
order, so the literal ``maintenance`` segment has to be declared first or it
would be parsed as a work-item UUID.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Response, status

from meobot.api.deps import CurrentActorDep, PrServicesDep, RequestIdDep
from meobot.api.routers.pr_work import work_item_detail
from meobot.api.schemas.pr_work import WorkItemDetailResponse
from meobot.api.schemas.pr_work_maintenance import (
    AdminNoteRequest,
    ContainerCleanupResponse,
    LegacyWorkItemDeleteResponse,
    MaintenancePreviewResponse,
    MaintenanceRunResponse,
    MaintenanceScopeRequest,
    TerminalWorkItemDeleteResponse,
    WorkTypeReferencesResponse,
)

router = APIRouter(prefix="/api/pr/work/maintenance", tags=["pr-work-maintenance"])

_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"description": "No usable session."},
    403: {"description": "Refused - PR_WORK_CONFIGURE."},
    404: {"description": "No such period, result, work item or work type."},
    409: {"description": "Refused by a business rule - see `details.reason`."},
    422: {"description": "The request itself is not valid."},
}


# --- Content sync -------------------------------------------------------------


@router.post(
    "/content-sync/preview",
    response_model=MaintenancePreviewResponse,
    summary="What a content sync would record",
    responses=_RESPONSES,
)
async def preview_content_sync(
    body: MaintenanceScopeRequest, actor: CurrentActorDep, services: PrServicesDep
) -> MaintenancePreviewResponse:
    """``PR_WORK_CONFIGURE``. Read-only: the projector's dry run over the scope."""
    preview = await services.work_maintenance.preview(actor=actor, scope=body.to_scope())
    return MaintenancePreviewResponse.from_preview(preview)


@router.post(
    "/content-sync/run",
    response_model=MaintenanceRunResponse,
    summary="Record the content results that are missing",
    responses=_RESPONSES,
)
async def run_content_sync(
    body: MaintenanceScopeRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> MaintenanceRunResponse:
    """``PR_WORK_CONFIGURE``. Additive and idempotent: only content with no
    result is projected, and a second run over the same scope writes nothing."""
    run = await services.work_maintenance.sync_missing(
        actor=actor, request_id=request_id, scope=body.to_scope()
    )
    return MaintenanceRunResponse.from_run(run)


# --- Content rebuild ----------------------------------------------------------


@router.post(
    "/content-rebuild/preview",
    response_model=MaintenancePreviewResponse,
    summary="What a rebuild from content would remove and recreate",
    responses=_RESPONSES,
)
async def preview_content_rebuild(
    body: MaintenanceScopeRequest, actor: CurrentActorDep, services: PrServicesDep
) -> MaintenancePreviewResponse:
    """``PR_WORK_CONFIGURE``. The same read-only preview, named for the act it precedes."""
    preview = await services.work_maintenance.preview(actor=actor, scope=body.to_scope())
    return MaintenancePreviewResponse.from_preview(preview)


@router.post(
    "/content-rebuild/run",
    response_model=MaintenanceRunResponse,
    summary="Rebuild content-derived work for an open period",
    responses=_RESPONSES,
)
async def run_content_rebuild(
    body: MaintenanceScopeRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> MaintenanceRunResponse:
    """``PR_WORK_CONFIGURE``, open period only.

    Counted content results filed under a type the mapping no longer names are
    taken out, then every piece of content in scope is projected exactly as the
    worker projects it. Manual and recurring work is not read.
    """
    run = await services.work_maintenance.rebuild(
        actor=actor, request_id=request_id, scope=body.to_scope(), note=body.note
    )
    return MaintenanceRunResponse.from_run(run)


# --- Removals -------------------------------------------------------------------


@router.post(
    "/results/{result_id}/admin-remove",
    response_model=WorkItemDetailResponse,
    summary="Take one result out of the actual, as an administrator",
    responses=_RESPONSES,
)
async def admin_remove_result(
    result_id: uuid.UUID,
    body: AdminNoteRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> WorkItemDetailResponse:
    """``PR_WORK_CONFIGURE``, open period only. An exclusion with a reason, never
    a delete; a content-derived result whose source is still accepted may be
    recorded again by the next projection."""
    result = await services.work_maintenance.admin_remove_result(
        actor=actor, request_id=request_id, result_id=result_id, note=body.note
    )
    return await work_item_detail(services, actor, result.work_item_id)


@router.post(
    "/containers/{work_item_id}/admin-remove",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Delete an empty period container",
    responses=_RESPONSES,
)
async def admin_remove_container(
    work_item_id: uuid.UUID,
    body: AdminNoteRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> Response:
    """``PR_WORK_CONFIGURE``, open period only, and only a container that holds
    no results and that nobody assigned."""
    await services.work_maintenance.remove_empty_container(
        actor=actor, request_id=request_id, work_item_id=work_item_id, note=body.note
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Legacy content work items ----------------------------------------------------


@router.delete(
    "/items/{work_item_id}",
    response_model=LegacyWorkItemDeleteResponse,
    summary="Delete one legacy content work item",
    responses=_RESPONSES,
)
async def delete_legacy_work_item(
    work_item_id: uuid.UUID,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
    note: str | None = None,
) -> LegacyWorkItemDeleteResponse:
    """``PR_WORK_CONFIGURE``, open period only, one row per call.

    Only a pre-``0039`` item-grain content work item is accepted - manual,
    recurring, a period container and a modern content result are refused with
    ``work_item_not_legacy_content``. The content item is untouched, nothing is
    re-recorded and no projection is queued: sync and rebuild stay separate
    acts under ``/content-sync`` and ``/content-rebuild``.
    """
    deletion = await services.work_maintenance.admin_delete_legacy_work_item(
        actor=actor, request_id=request_id, work_item_id=work_item_id, note=note
    )
    return LegacyWorkItemDeleteResponse.from_deletion(deletion)


# --- Terminal work items (cancelled or rejected) --------------------------------


@router.delete(
    "/terminal-items/{work_item_id}",
    response_model=TerminalWorkItemDeleteResponse,
    summary="Delete one cancelled or rejected work item",
    responses=_RESPONSES,
)
async def delete_terminal_work_item(
    work_item_id: uuid.UUID,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
    note: str | None = None,
) -> TerminalWorkItemDeleteResponse:
    """``PR_WORK_CONFIGURE``, one row per call, and only a ``CANCELLED`` or
    ``REJECTED`` row.

    A second eligibility rule beside ``DELETE /items/{id}`` and not a widening
    of it: that route is about provenance (a legacy content projection), this
    one about lifecycle (work that ended without being done, whatever its
    source). Hard-delete is maintenance, not a transition: a rejected proposal
    needs no detour through ``CANCELLED`` - there is no such edge - to be
    cleaned up. Anything still in flight or approved is refused with
    ``work_item_not_terminal``; a period container, or a terminal row that
    still holds a result, a counted contribution or an M2/M6 allocation, with
    ``terminal_work_item_delete_blocked`` and the counts. Nothing is
    projected, synced or recomputed afterwards, and a modern result is never
    removed through here - that is *Xóa kết quả*.
    """
    deletion = await services.work_maintenance.admin_delete_terminal_work_item(
        actor=actor, request_id=request_id, work_item_id=work_item_id, note=note
    )
    return TerminalWorkItemDeleteResponse.from_deletion(deletion)


# --- Work types -----------------------------------------------------------------


@router.get(
    "/work-types/{work_type_id}/references",
    response_model=WorkTypeReferencesResponse,
    summary="What still refers to a work type",
    responses=_RESPONSES,
)
async def work_type_references(
    work_type_id: uuid.UUID, actor: CurrentActorDep, services: PrServicesDep
) -> WorkTypeReferencesResponse:
    """``PR_WORK_CONFIGURE``. The counts a delete would be refused with."""
    refs = await services.work_maintenance.work_type_references(
        actor=actor, work_type_id=work_type_id
    )
    return WorkTypeReferencesResponse.from_references(refs)


@router.post(
    "/work-types/{work_type_id}/cleanup-empty-containers",
    response_model=ContainerCleanupResponse,
    summary="Delete the empty containers under a work type",
    responses=_RESPONSES,
)
async def cleanup_empty_containers(
    work_type_id: uuid.UUID,
    body: AdminNoteRequest,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
) -> ContainerCleanupResponse:
    """``PR_WORK_CONFIGURE``. The step between a rebuild that refiled a type's
    results elsewhere and deleting the type: the streams it left empty."""
    ids = await services.work_maintenance.removable_empty_containers(
        actor=actor, work_type_id=work_type_id
    )
    for work_item_id in ids:
        await services.work_maintenance.remove_empty_container(
            actor=actor, request_id=request_id, work_item_id=work_item_id, note=body.note
        )
    refs = await services.work_maintenance.work_type_references(
        actor=actor, work_type_id=work_type_id
    )
    return ContainerCleanupResponse(
        removed=len(ids), remaining_references=WorkTypeReferencesResponse.from_references(refs)
    )


@router.delete(
    "/work-types/{work_type_id}",
    response_model=WorkTypeReferencesResponse,
    summary="Delete a work type nothing refers to",
    responses=_RESPONSES,
)
async def delete_work_type(
    work_type_id: uuid.UUID,
    actor: CurrentActorDep,
    services: PrServicesDep,
    request_id: RequestIdDep,
    note: str | None = None,
) -> WorkTypeReferencesResponse:
    """``PR_WORK_CONFIGURE``. Refused, with every blocking count, while anything
    still refers to the type. Its inactive content mappings go with it."""
    refs = await services.work_maintenance.delete_work_type(
        actor=actor, request_id=request_id, work_type_id=work_type_id, note=note
    )
    return WorkTypeReferencesResponse.from_references(refs)


__all__: list[str] = ["router"]
