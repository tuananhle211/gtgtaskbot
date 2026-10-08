"""Google Drive endpoints: status, allowed folders, templates, created files.

Internal only. Like every other route in this API these are bound to
``127.0.0.1`` on the NAS and carry no authentication yet - see
:func:`meobot.api.deps.get_current_system_actor`. Do not publish this API.

Business logic stays in the application services; these functions parse, call
and shape.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status

from meobot.api.deps import (
    ActorDep,
    DriveClientDep,
    DriveFolderServiceDep,
    RequestIdDep,
    SettingsDep,
    SheetTemplateServiceDep,
    SpreadsheetCreationServiceDep,
)
from meobot.api.schemas.drive import (
    CreatedSpreadsheetResponse,
    CreateSpreadsheetRequest,
    CreateTemplateRequest,
    DriveFolderResponse,
    DriveStatusResponse,
    RegisterFolderRequest,
    SheetTemplateResponse,
    UpdateTemplateRequest,
)
from meobot.core.errors import MeoBotError, ValidationError
from meobot.db.models.drive import CreatedSpreadsheet, DriveFolder, SheetTemplate
from meobot.domain.drive.models import (
    SpreadsheetRequest,
    TemplateKind,
    extract_folder_id,
)
from meobot.domain.drive.templates import template_for_kind

router = APIRouter(prefix="/api/v1", tags=["drive"])


def _folder_response(folder: DriveFolder) -> DriveFolderResponse:
    return DriveFolderResponse(
        id=folder.id,
        drive_folder_id=folder.drive_folder_id,
        shared_drive_id=folder.shared_drive_id,
        name=folder.name,
        path_label=folder.path_label,
        purpose=folder.purpose,
        team_scope=folder.team_scope,
        active=folder.active,
        validation_status=folder.validation_status,
        validation_error=folder.validation_error,
        last_validated_at=folder.last_validated_at,
        created_at=folder.created_at,
    )


def _template_response(template: SheetTemplate) -> SheetTemplateResponse:
    return SheetTemplateResponse(
        id=template.id,
        code=template.code,
        name=template.name,
        description=template.description,
        kind=template.kind,
        version=template.version,
        active=template.active,
        has_source_file=bool(template.source_file_id),
        default_worksheet_name=template.default_worksheet_name,
        expected_tabs=list(template.expected_tabs.get("tabs", [])),
        default_field_mapping=dict(template.default_field_mapping),
        default_write_back_mapping=dict(template.default_write_back_mapping),
    )


def _created_response(record: CreatedSpreadsheet) -> CreatedSpreadsheetResponse:
    return CreatedSpreadsheetResponse(
        id=record.id,
        name=record.name,
        kind=record.kind,
        template_code=record.template_code,
        template_version=record.template_version,
        spreadsheet_id=record.spreadsheet_id,
        spreadsheet_url=record.spreadsheet_url,
        parent_folder_id=record.parent_folder_id,
        shared_drive_id=record.shared_drive_id,
        creation_method=record.creation_method,
        creation_status=record.creation_status,
        creation_error=record.creation_error,
        sheet_profile_id=record.sheet_profile_id,
        created_by_telegram_id=record.created_by_telegram_id,
        created_at=record.created_at,
    )


# --- Status ----------------------------------------------------------------
@router.get("/drive/status", response_model=DriveStatusResponse, summary="Drive configuration")
async def drive_status(
    settings: SettingsDep,
    folders: DriveFolderServiceDep,
    drive: DriveClientDep,
) -> DriveStatusResponse:
    """Report Drive configuration and whether Drive answers."""
    reachable = False
    message: str | None = None
    probe = settings.google_drive_root_folder_id or settings.google_shared_drive_id
    if settings.google_enabled and probe:
        try:
            await drive.get_file(probe)
            reachable = True
        except MeoBotError as exc:
            message = exc.message
    elif not settings.google_enabled:
        message = "GOOGLE_SERVICE_ACCOUNT_FILE is not configured."
    else:
        message = "No root folder or shared drive configured to probe."

    registered = list(await folders.list_folders(active_only=False))
    usable = [folder for folder in registered if folder.validation_status == "valid"]
    return DriveStatusResponse(
        credentials_configured=settings.google_enabled,
        drive_reachable=reachable,
        drive_message=message,
        root_folder_configured=bool(settings.google_drive_root_folder_id),
        shared_drive_configured=bool(settings.google_shared_drive_id),
        work_template_configured=bool(settings.google_work_sheet_template_id),
        script_template_configured=bool(settings.google_script_sheet_template_id),
        allowed_folder_count=len(registered),
        usable_folder_count=len(usable),
    )


# --- Folders ---------------------------------------------------------------
@router.get(
    "/drive/folders",
    response_model=list[DriveFolderResponse],
    summary="List allowed destination folders",
)
async def list_drive_folders(
    folders: DriveFolderServiceDep,
    active_only: bool = Query(default=False, description="Only return active folders."),
) -> list[DriveFolderResponse]:
    """List the folders TasksBot may create files in."""
    return [
        _folder_response(folder) for folder in await folders.list_folders(active_only=active_only)
    ]


@router.post(
    "/drive/folders",
    response_model=DriveFolderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register an allowed destination folder",
)
async def register_drive_folder(
    payload: RegisterFolderRequest,
    folders: DriveFolderServiceDep,
    actor: ActorDep,
    request_id: RequestIdDep,
) -> DriveFolderResponse:
    """Validate a folder against Google and register it as a destination."""
    folder_id = extract_folder_id(payload.folder)
    if folder_id is None:
        raise ValidationError("Not a recognisable Drive folder URL or id.")
    folder = await folders.register(
        actor=actor,
        request_id=request_id,
        drive_folder_id=folder_id,
        path_label=payload.path_label,
        purpose=payload.purpose,
        team_scope=payload.team_scope,
    )
    return _folder_response(folder)


@router.post(
    "/drive/folders/{folder_id}/validate",
    response_model=DriveFolderResponse,
    summary="Re-validate a registered folder",
)
async def validate_drive_folder(
    folder_id: uuid.UUID,
    folders: DriveFolderServiceDep,
    actor: ActorDep,
    request_id: RequestIdDep,
) -> DriveFolderResponse:
    """Re-check a registered folder's existence, access and create permission."""
    folder = await folders.revalidate(actor=actor, request_id=request_id, folder_id=folder_id)
    return _folder_response(folder)


# --- Templates -------------------------------------------------------------
@router.get(
    "/sheet-templates",
    response_model=list[SheetTemplateResponse],
    summary="List sheet templates",
)
async def list_sheet_templates(
    templates: SheetTemplateServiceDep,
    active_only: bool = Query(default=False),
) -> list[SheetTemplateResponse]:
    """List the templates TasksBot can produce, seeding the built-ins."""
    await templates.ensure_all_builtin()
    return [
        _template_response(template)
        for template in await templates.list_templates(active_only=active_only)
    ]


@router.post(
    "/sheet-templates",
    response_model=SheetTemplateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Seed a built-in sheet template",
)
async def create_sheet_template(
    payload: CreateTemplateRequest,
    templates: SheetTemplateServiceDep,
) -> SheetTemplateResponse:
    """Seed one built-in template.

    New template *layouts* are a code change on purpose: the columns imply the
    Sheet-Profile mapping, and a layout invented through an API would have no
    mapping to go with it.
    """
    template = await templates.ensure_builtin(template_for_kind(payload.kind))
    return _template_response(template)


@router.patch(
    "/sheet-templates/{template_id}",
    response_model=SheetTemplateResponse,
    summary="Update a sheet template",
)
async def update_sheet_template(
    template_id: uuid.UUID,
    payload: UpdateTemplateRequest,
    templates: SheetTemplateServiceDep,
    actor: ActorDep,
    request_id: RequestIdDep,
) -> SheetTemplateResponse:
    """Activate/deactivate a template, or repoint it at another source file."""
    template = await templates.get(template_id)
    if payload.active is not None:
        template = await templates.set_active(
            actor=actor, request_id=request_id, template_id=template_id, active=payload.active
        )
    if payload.source_file_id is not None:
        template = await templates.set_source_file(
            actor=actor,
            request_id=request_id,
            template_id=template_id,
            source_file_id=payload.source_file_id or None,
        )
    return _template_response(template)


# --- Created spreadsheets --------------------------------------------------
@router.get(
    "/created-spreadsheets",
    response_model=list[CreatedSpreadsheetResponse],
    summary="List spreadsheets TasksBot created",
)
async def list_created_spreadsheets(
    creation: SpreadsheetCreationServiceDep,
    limit: int = Query(default=20, ge=1, le=100),
) -> list[CreatedSpreadsheetResponse]:
    """List the spreadsheets TasksBot created, newest first."""
    return [_created_response(record) for record in await creation.list_created(limit=limit)]


@router.get(
    "/created-spreadsheets/{record_id}",
    response_model=CreatedSpreadsheetResponse,
    summary="Get one created spreadsheet",
)
async def get_created_spreadsheet(
    record_id: uuid.UUID,
    creation: SpreadsheetCreationServiceDep,
) -> CreatedSpreadsheetResponse:
    """Fetch one creation record."""
    return _created_response(await creation.get(record_id))


# --- Creation --------------------------------------------------------------
async def _create(
    payload: CreateSpreadsheetRequest,
    kind: TemplateKind,
    folders: DriveFolderServiceDep,
    templates: SheetTemplateServiceDep,
    creation: SpreadsheetCreationServiceDep,
    actor: ActorDep,
    request_id: RequestIdDep,
) -> CreatedSpreadsheetResponse:
    """Shared body of the two creation endpoints."""
    folder = await folders.resolve_destination(payload.folder)
    folders.assert_usable_by(folder, actor)
    template = await templates.for_kind(kind)

    request = SpreadsheetRequest(
        template_code=template.code,
        template_version=template.version,
        name=payload.name,
        folder_id=folder.drive_folder_id,
        kind=kind,
        team=payload.team,
        channel=payload.channel,
        campaign=payload.campaign,
        period=payload.period,
        actor_reference=str(actor.telegram_user_id or actor.user_id or "api"),
        # Callers that want retry-safety supply their own reference; otherwise
        # the correlation id makes each request distinct, which is the correct
        # default for a fresh request.
        confirmation_reference=payload.idempotency_reference or str(request_id),
        register_profile=kind is TemplateKind.SCRIPT_MANAGEMENT,
        run_initial_sync=False,
    )
    outcome = await creation.create(actor=actor, request_id=request_id, request=request)
    return _created_response(outcome.record)


@router.post(
    "/spreadsheets/create-work",
    response_model=CreatedSpreadsheetResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a work-management spreadsheet",
)
async def create_work_spreadsheet(
    payload: CreateSpreadsheetRequest,
    folders: DriveFolderServiceDep,
    templates: SheetTemplateServiceDep,
    creation: SpreadsheetCreationServiceDep,
    actor: ActorDep,
    request_id: RequestIdDep,
) -> CreatedSpreadsheetResponse:
    """Create a work-management Sheet. Never registers a Sheet Profile."""
    return await _create(
        payload,
        TemplateKind.WORK_MANAGEMENT,
        folders,
        templates,
        creation,
        actor,
        request_id,
    )


@router.post(
    "/spreadsheets/create-script",
    response_model=CreatedSpreadsheetResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a script-management spreadsheet",
)
async def create_script_spreadsheet(
    payload: CreateSpreadsheetRequest,
    folders: DriveFolderServiceDep,
    templates: SheetTemplateServiceDep,
    creation: SpreadsheetCreationServiceDep,
    actor: ActorDep,
    request_id: RequestIdDep,
) -> CreatedSpreadsheetResponse:
    """Create a script-management Sheet and register its Sheet Profile."""
    return await _create(
        payload,
        TemplateKind.SCRIPT_MANAGEMENT,
        folders,
        templates,
        creation,
        actor,
        request_id,
    )
