"""Sheet profile schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from meobot.application.script_sync_service import SyncReport
from meobot.application.sheet_inspection_service import SheetInspection
from meobot.db.models.sheet_profile import SheetProfile


class SheetProfileResponse(BaseModel):
    """A sheet profile as exposed by the API."""

    id: uuid.UUID
    name: str
    spreadsheet_id: str
    spreadsheet_url: str | None
    sheet_name: str
    header_row: int
    channel: str | None
    script_type_id: uuid.UUID | None
    field_mapping: dict[str, Any]
    status_mapping: dict[str, Any]
    write_back_mapping: dict[str, Any]
    schema_fingerprint: str | None
    state: str
    active: bool
    script_count: int | None
    last_synced_at: datetime | None
    last_sync_status: str | None
    last_sync_error: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(
        cls,
        model: SheetProfile,
        *,
        script_count: int | None = None,
    ) -> SheetProfileResponse:
        """Map an ORM row onto the wire schema."""
        return cls(
            id=model.id,
            name=model.name,
            spreadsheet_id=model.spreadsheet_id,
            spreadsheet_url=model.spreadsheet_url,
            sheet_name=model.sheet_name,
            header_row=model.header_row,
            channel=model.channel,
            script_type_id=model.script_type_id,
            field_mapping=dict(model.field_mapping),
            status_mapping=dict(model.status_mapping),
            write_back_mapping=dict(model.write_back_mapping),
            schema_fingerprint=model.schema_fingerprint,
            state=model.state.value,
            active=model.active,
            script_count=script_count,
            last_synced_at=model.last_synced_at,
            last_sync_status=model.last_sync_status,
            last_sync_error=model.last_sync_error,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )


class CreateSheetProfileRequest(BaseModel):
    """Body for ``POST /api/v1/sheet-profiles``."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    spreadsheet_id: str = Field(min_length=1, max_length=200)
    sheet_name: str = Field(min_length=1, max_length=200)
    header_row: int = Field(default=1, ge=1, le=50)
    channel: str | None = Field(default=None, max_length=100)
    script_type_id: uuid.UUID | None = None
    field_mapping: dict[str, Any] = Field(
        description=(
            "Canonical field -> sheet header(s). Must cover script_body. A "
            "value may be a string or a list of candidate headers."
        )
    )
    status_mapping: dict[str, str] = Field(
        default_factory=dict,
        description="Raw sheet status text -> ScriptStatus value.",
    )
    write_back_mapping: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Optional write-back targets -> sheet header. Only the columns "
            "named here are ever written."
        ),
    )
    headers: list[str] | None = Field(
        default=None,
        description="Optional header row snapshot; stored as a schema fingerprint.",
    )


class UpdateSheetProfileRequest(BaseModel):
    """Body for ``PATCH /api/v1/sheet-profiles/{id}``."""

    model_config = ConfigDict(extra="forbid")

    field_mapping: dict[str, Any]
    status_mapping: dict[str, str] | None = None
    write_back_mapping: dict[str, str] | None = None
    headers: list[str] | None = None


class InspectSheetRequest(BaseModel):
    """Body for ``POST /api/v1/sheet-profiles/{id}/inspect``."""

    model_config = ConfigDict(extra="forbid")

    sheet_name: str | None = Field(default=None, max_length=200)
    header_row: int | None = Field(default=None, ge=1, le=50)
    use_llm: bool = Field(
        default=False,
        description="Consult the LLM when the alias table leaves a gap.",
    )


class RemapSheetProfileRequest(BaseModel):
    """Body for ``POST /api/v1/sheet-profiles/{id}/remap``."""

    model_config = ConfigDict(extra="forbid")

    field_mapping: dict[str, Any] | None = Field(
        default=None,
        description="Omit to adopt the mapping MeoBot proposes for the live sheet.",
    )
    headers: list[str] | None = None
    use_llm: bool = False


class SheetInspectionResponse(BaseModel):
    """What an inspection found."""

    spreadsheet_id: str
    spreadsheet_title: str
    sheet_name: str
    header_row: int
    headers: list[str]
    sample_rows: list[dict[str, str]]
    proposed_mapping: dict[str, str]
    proposed_write_back: dict[str, str]
    unmapped_headers: list[str]
    missing_fields: list[str]
    confidence: float
    fingerprint: str
    source: str

    @classmethod
    def from_inspection(cls, inspection: SheetInspection) -> SheetInspectionResponse:
        return cls(
            spreadsheet_id=inspection.spreadsheet_id,
            spreadsheet_title=inspection.spreadsheet_title,
            sheet_name=inspection.sheet_name,
            header_row=inspection.header_row,
            headers=list(inspection.headers),
            sample_rows=list(inspection.sample_rows),
            proposed_mapping=dict(inspection.proposal.mapping),
            proposed_write_back=dict(inspection.write_back),
            unmapped_headers=list(inspection.proposal.unmapped_headers),
            missing_fields=inspection.missing_fields,
            confidence=inspection.proposal.confidence,
            fingerprint=inspection.fingerprint,
            source=inspection.source,
        )


class SyncReportResponse(BaseModel):
    """What one synchronisation run did."""

    profile_id: uuid.UUID
    profile_name: str
    created: int
    new_versions: int
    unchanged: int
    duplicates: list[str]
    row_errors: list[str]
    invalidated_approvals: list[uuid.UUID]
    needs_remap: bool
    missing_headers: list[str]
    failed: bool
    error: str | None

    @classmethod
    def from_report(cls, report: SyncReport) -> SyncReportResponse:
        return cls(
            profile_id=report.profile_id,
            profile_name=report.profile_name,
            created=report.created,
            new_versions=report.new_versions,
            unchanged=report.unchanged,
            duplicates=list(report.duplicates),
            row_errors=[f"row {number}: {message}" for number, message in report.row_errors],
            invalidated_approvals=list(report.invalidated_approvals),
            needs_remap=report.needs_remap,
            missing_headers=list(report.missing_headers),
            failed=report.failed,
            error=report.error,
        )


class SheetProfileListResponse(BaseModel):
    """Envelope for sheet profile listings."""

    items: list[SheetProfileResponse]
    total: int
