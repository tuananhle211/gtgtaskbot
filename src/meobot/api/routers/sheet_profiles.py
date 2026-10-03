"""Sheet profile endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status

from meobot.api.deps import (
    ActorDep,
    RequestIdDep,
    SheetInspectionServiceDep,
    SheetProfileServiceDep,
    SheetSyncServiceDep,
)
from meobot.api.schemas.sheet_profiles import (
    CreateSheetProfileRequest,
    InspectSheetRequest,
    RemapSheetProfileRequest,
    SheetInspectionResponse,
    SheetProfileListResponse,
    SheetProfileResponse,
    SyncReportResponse,
    UpdateSheetProfileRequest,
)

router = APIRouter(prefix="/api/v1/sheet-profiles", tags=["sheet-profiles"])


@router.get("", response_model=SheetProfileListResponse, summary="List sheet profiles")
async def list_sheet_profiles(
    service: SheetProfileServiceDep,
    active_only: bool = Query(default=True, description="Only return active profiles."),
) -> SheetProfileListResponse:
    """List configured sheet profiles."""
    profiles = await service.list_profiles(active_only=active_only)
    counts = await service.script_counts()
    items = [
        SheetProfileResponse.from_model(item, script_count=counts.get(item.id, 0))
        for item in profiles
    ]
    return SheetProfileListResponse(items=items, total=len(items))


@router.post(
    "",
    response_model=SheetProfileResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a sheet profile",
    responses={
        409: {"description": "This spreadsheet tab is already configured"},
        422: {"description": "The field mapping is incomplete or invalid"},
    },
)
async def create_sheet_profile(
    payload: CreateSheetProfileRequest,
    service: SheetProfileServiceDep,
    actor: ActorDep,
    request_id: RequestIdDep,
) -> SheetProfileResponse:
    """Create a profile describing how to read one spreadsheet tab.

    The mapping must cover ``script_body``; every ``status_mapping`` target
    must be a known ``ScriptStatus``.
    """
    profile = await service.create_profile(
        actor=actor,
        request_id=request_id,
        name=payload.name,
        spreadsheet_id=payload.spreadsheet_id,
        sheet_name=payload.sheet_name,
        field_mapping=payload.field_mapping,
        header_row=payload.header_row,
        channel=payload.channel,
        script_type_id=payload.script_type_id,
        status_mapping=payload.status_mapping,
        headers=payload.headers,
        write_back_mapping=payload.write_back_mapping,
    )
    return SheetProfileResponse.from_model(profile)


@router.get(
    "/{profile_id}",
    response_model=SheetProfileResponse,
    summary="Get one sheet profile",
    responses={404: {"description": "No such profile"}},
)
async def get_sheet_profile(
    profile_id: uuid.UUID,
    service: SheetProfileServiceDep,
) -> SheetProfileResponse:
    """Return one profile."""
    profile = await service.get(profile_id)
    counts = await service.script_counts()
    return SheetProfileResponse.from_model(profile, script_count=counts.get(profile.id, 0))


@router.patch(
    "/{profile_id}",
    response_model=SheetProfileResponse,
    summary="Update a profile's mapping",
    responses={
        404: {"description": "No such profile"},
        422: {"description": "The new mapping is invalid"},
    },
)
async def update_sheet_profile(
    profile_id: uuid.UUID,
    payload: UpdateSheetProfileRequest,
    service: SheetProfileServiceDep,
    actor: ActorDep,
    request_id: RequestIdDep,
) -> SheetProfileResponse:
    """Replace the mapping and clear a ``schema_changed`` state."""
    profile = await service.update_mapping(
        actor=actor,
        request_id=request_id,
        profile_id=profile_id,
        field_mapping=payload.field_mapping,
        headers=payload.headers,
        status_mapping=payload.status_mapping,
        write_back_mapping=payload.write_back_mapping,
    )
    return SheetProfileResponse.from_model(profile)


@router.post(
    "/{profile_id}/inspect",
    response_model=SheetInspectionResponse,
    summary="Read the live sheet and propose a mapping",
    responses={
        404: {"description": "No such profile, or the tab is gone"},
        400: {"description": "Google is not configured or refused access"},
    },
)
async def inspect_sheet_profile(
    profile_id: uuid.UUID,
    payload: InspectSheetRequest,
    profiles: SheetProfileServiceDep,
    inspector: SheetInspectionServiceDep,
) -> SheetInspectionResponse:
    """Re-read the sheet's headers and sample rows, and propose a mapping."""
    profile = await profiles.get(profile_id)
    inspection = await inspector.inspect(
        spreadsheet_id=profile.spreadsheet_id,
        sheet_name=payload.sheet_name or profile.sheet_name,
        header_row=payload.header_row or profile.header_row,
        use_llm=payload.use_llm,
    )
    return SheetInspectionResponse.from_inspection(inspection)


@router.post(
    "/{profile_id}/sync",
    response_model=SyncReportResponse,
    summary="Synchronise this profile now",
    responses={404: {"description": "No such profile"}},
)
async def sync_sheet_profile(
    profile_id: uuid.UUID,
    profiles: SheetProfileServiceDep,
    syncer: SheetSyncServiceDep,
    actor: ActorDep,
    request_id: RequestIdDep,
) -> SyncReportResponse:
    """Run a synchronisation inline.

    The Telegram path enqueues ``sheets.sync_profile`` instead; this endpoint
    is for development and for scripted backfills.
    """
    profile = await profiles.get(profile_id)
    report = await syncer.sync_profile(actor=actor, request_id=request_id, profile=profile)
    return SyncReportResponse.from_report(report)


@router.post(
    "/{profile_id}/remap",
    response_model=SheetProfileResponse,
    summary="Re-map a profile after a schema change",
    responses={
        404: {"description": "No such profile"},
        422: {"description": "The proposed mapping is invalid"},
    },
)
async def remap_sheet_profile(
    profile_id: uuid.UUID,
    payload: RemapSheetProfileRequest,
    profiles: SheetProfileServiceDep,
    inspector: SheetInspectionServiceDep,
    actor: ActorDep,
    request_id: RequestIdDep,
) -> SheetProfileResponse:
    """Adopt a new mapping and return the profile to ``active``.

    With no mapping in the body, the freshly proposed one is adopted - which
    is the fast path after a column was renamed.
    """
    profile = await profiles.get(profile_id)
    mapping = payload.field_mapping
    headers = payload.headers

    if mapping is None or headers is None:
        inspection = await inspector.inspect(
            spreadsheet_id=profile.spreadsheet_id,
            sheet_name=profile.sheet_name,
            header_row=profile.header_row,
            use_llm=payload.use_llm,
        )
        mapping = mapping or dict(inspection.proposal.mapping)
        headers = headers or inspection.headers

    updated = await profiles.update_mapping(
        actor=actor,
        request_id=request_id,
        profile_id=profile_id,
        field_mapping=mapping,
        headers=headers,
    )
    return SheetProfileResponse.from_model(updated)


@router.post(
    "/{profile_id}/deactivate",
    response_model=SheetProfileResponse,
    summary="Stop synchronising a profile",
    responses={404: {"description": "No such profile"}},
)
async def deactivate_sheet_profile(
    profile_id: uuid.UUID,
    service: SheetProfileServiceDep,
    actor: ActorDep,
    request_id: RequestIdDep,
) -> SheetProfileResponse:
    """Deactivate a profile. Imported scripts are kept."""
    profile = await service.deactivate(
        actor=actor,
        request_id=request_id,
        profile_id=profile_id,
    )
    return SheetProfileResponse.from_model(profile)
