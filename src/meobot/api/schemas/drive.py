"""Request and response models for the Drive and spreadsheet endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from meobot.domain.drive.models import TemplateKind


class DriveStatusResponse(BaseModel):
    """What ``GET /api/v1/drive/status`` reports.

    Deliberately carries no full folder or template id: those are not secrets,
    but an internal status endpoint has no reason to hand them out in bulk.
    """

    model_config = ConfigDict(frozen=True)

    credentials_configured: bool
    drive_reachable: bool
    drive_message: str | None = None
    root_folder_configured: bool
    shared_drive_configured: bool
    work_template_configured: bool
    script_template_configured: bool
    allowed_folder_count: int
    usable_folder_count: int


class DriveFolderResponse(BaseModel):
    """One registered destination folder."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    drive_folder_id: str
    shared_drive_id: str | None
    name: str
    path_label: str | None
    purpose: str | None
    team_scope: str | None
    active: bool
    validation_status: str
    validation_error: str | None
    last_validated_at: datetime | None
    created_at: datetime | None


class RegisterFolderRequest(BaseModel):
    """Body of ``POST /api/v1/drive/folders``."""

    model_config = ConfigDict(extra="forbid")

    folder: str = Field(min_length=1, max_length=500, description="Drive folder URL or id.")
    path_label: str | None = Field(default=None, max_length=500)
    purpose: str | None = Field(default=None, max_length=300)
    team_scope: str | None = Field(default=None, max_length=100)


class SheetTemplateResponse(BaseModel):
    """One registered sheet template."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    code: str
    name: str
    description: str | None
    kind: str
    version: int
    active: bool
    has_source_file: bool
    default_worksheet_name: str
    expected_tabs: list[str]
    default_field_mapping: dict[str, str]
    default_write_back_mapping: dict[str, str]


class CreateTemplateRequest(BaseModel):
    """Body of ``POST /api/v1/sheet-templates``.

    Only the built-in templates can be created; this endpoint seeds them. New
    template *layouts* are a code change, because the Sheet-Profile mapping
    they imply is code.
    """

    model_config = ConfigDict(extra="forbid")

    kind: TemplateKind


class UpdateTemplateRequest(BaseModel):
    """Body of ``PATCH /api/v1/sheet-templates/{id}``."""

    model_config = ConfigDict(extra="forbid")

    active: bool | None = None
    source_file_id: str | None = Field(default=None, max_length=200)


class CreatedSpreadsheetResponse(BaseModel):
    """One spreadsheet MeoBot created."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    name: str
    kind: str
    template_code: str | None
    template_version: int | None
    spreadsheet_id: str | None
    spreadsheet_url: str | None
    parent_folder_id: str | None
    shared_drive_id: str | None
    creation_method: str | None
    creation_status: str
    creation_error: str | None
    sheet_profile_id: uuid.UUID | None
    created_by_telegram_id: int | None
    created_at: datetime | None


class CreateSpreadsheetRequest(BaseModel):
    """Body of the two spreadsheet-creation endpoints."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=300)
    folder: str = Field(
        min_length=1,
        max_length=500,
        description="A registered folder: its UUID prefix, its name, or its Drive id.",
    )
    period: str | None = Field(default=None, max_length=100)
    team: str | None = Field(default=None, max_length=100)
    channel: str | None = Field(default=None, max_length=100)
    campaign: str | None = Field(default=None, max_length=200)
    idempotency_reference: str | None = Field(
        default=None,
        max_length=100,
        description=(
            "Repeat the same value to make a retry return the existing file "
            "instead of creating another one."
        ),
    )
