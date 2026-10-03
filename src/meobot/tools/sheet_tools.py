"""Sheet profile tools.

``sheet_profile.create`` is the canonical *medium* risk, state-changing tool:
allowed without a confirmation token, but permissioned, validated and audited.
``sheet_profile.deactivate`` is high-risk because it silently stops a team's
scripts from arriving, which is the kind of change somebody should have to
confirm.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from meobot.application.audit_service import AuditService
from meobot.application.script_sync_service import ScriptSyncService, summarize_reports
from meobot.application.sheet_inspection_service import SheetInspectionService
from meobot.application.sheet_profile_service import SheetProfileService
from meobot.core.time import format_local
from meobot.domain.permissions.matrix import Permission
from meobot.domain.policy.models import RiskLevel
from meobot.domain.sheets.models import extract_spreadsheet_id
from meobot.integrations.google.sheets import SheetsClient
from meobot.tools.base import NoArguments, ToolContext, ToolDefinition, ToolResult

#: Sample rows shown by ``sheet_profile.inspect``.
INSPECT_SAMPLE_ROWS = 3


class CreateSheetProfileArgs(BaseModel):
    """Arguments for ``sheet_profile.create``."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    spreadsheet_id: str = Field(min_length=1, max_length=200)
    sheet_name: str = Field(min_length=1, max_length=200)
    field_mapping: dict[str, Any]
    header_row: int = Field(default=1, ge=1, le=50)
    channel: str | None = Field(default=None, max_length=100)
    status_mapping: dict[str, str] = Field(default_factory=dict)
    write_back_mapping: dict[str, str] = Field(default_factory=dict)


class InspectSheetArgs(BaseModel):
    """Arguments for ``sheet_profile.inspect``."""

    model_config = ConfigDict(extra="forbid")

    spreadsheet: str = Field(min_length=1, max_length=500, description="URL hoặc spreadsheet id.")
    sheet_name: str | None = Field(default=None, max_length=200)
    header_row: int = Field(default=1, ge=1, le=50)


class ProfileReferenceArgs(BaseModel):
    """Arguments for tools addressing one profile."""

    model_config = ConfigDict(extra="forbid")

    profile_id: uuid.UUID


class UpdateMappingArgs(BaseModel):
    """Arguments for ``sheet_profile.update``."""

    model_config = ConfigDict(extra="forbid")

    profile_id: uuid.UUID
    field_mapping: dict[str, Any]
    status_mapping: dict[str, str] | None = None
    write_back_mapping: dict[str, str] | None = None


async def _list_handler(context: ToolContext, arguments: NoArguments) -> ToolResult:
    session = context.require_session()
    service = SheetProfileService(session, AuditService(session))
    profiles = await service.list_profiles(active_only=True)
    if not profiles:
        return ToolResult(success=True, message="Chưa có sheet profile nào.", data={"items": []})

    counts = await service.script_counts()
    timezone = context.settings.timezone
    lines: list[str] = ["📊 Sheet profile đang hoạt động:"]
    for item in profiles:
        synced = (
            format_local(item.last_synced_at, timezone, "%d/%m %H:%M")
            if item.last_synced_at
            else "chưa đồng bộ"
        )
        lines.append(
            f"• {item.name} — tab {item.sheet_name!r} · {item.state.value} · "
            f"{counts.get(item.id, 0)} kịch bản · đồng bộ: {synced}"
        )
    return ToolResult(
        success=True,
        message="\n".join(lines),
        data={
            "items": [
                {
                    "id": str(item.id),
                    "name": item.name,
                    "spreadsheet_id": item.spreadsheet_id,
                    "sheet_name": item.sheet_name,
                    "channel": item.channel,
                    "state": item.state.value,
                    "script_count": counts.get(item.id, 0),
                    "last_synced_at": (
                        item.last_synced_at.isoformat() if item.last_synced_at else None
                    ),
                }
                for item in profiles
            ]
        },
        entity_type="sheet_profile",
    )


async def _create_handler(context: ToolContext, arguments: CreateSheetProfileArgs) -> ToolResult:
    session = context.require_session()
    service = SheetProfileService(session, AuditService(session))
    profile = await service.create_profile(
        actor=context.actor,
        request_id=context.request_id,
        name=arguments.name,
        spreadsheet_id=arguments.spreadsheet_id,
        sheet_name=arguments.sheet_name,
        field_mapping=arguments.field_mapping,
        header_row=arguments.header_row,
        channel=arguments.channel,
        status_mapping=arguments.status_mapping,
        write_back_mapping=arguments.write_back_mapping,
    )
    return ToolResult(
        success=True,
        message=f"✅ Đã tạo sheet profile {profile.name!r}.",
        data={"id": str(profile.id), "name": profile.name},
        entity_type="sheet_profile",
        entity_id=str(profile.id),
    )


async def _update_handler(context: ToolContext, arguments: UpdateMappingArgs) -> ToolResult:
    session = context.require_session()
    service = SheetProfileService(session, AuditService(session))
    profile = await service.update_mapping(
        actor=context.actor,
        request_id=context.request_id,
        profile_id=arguments.profile_id,
        field_mapping=arguments.field_mapping,
        status_mapping=arguments.status_mapping,
        write_back_mapping=arguments.write_back_mapping,
    )
    return ToolResult(
        success=True,
        message=f"✅ Đã cập nhật mapping cho {profile.name!r}.",
        data={"id": str(profile.id), "state": profile.state.value},
        entity_type="sheet_profile",
        entity_id=str(profile.id),
    )


def build_sheet_tools(*, sheets: SheetsClient) -> list[ToolDefinition]:
    """Tools for inspecting, configuring and synchronising sheet profiles.

    Args:
        sheets: Google Sheets client used by the inspect and sync tools.
    """

    async def inspect_handler(context: ToolContext, arguments: InspectSheetArgs) -> ToolResult:
        spreadsheet_id = extract_spreadsheet_id(arguments.spreadsheet)
        if spreadsheet_id is None:
            return ToolResult(
                success=False,
                message="Không nhận ra Google Sheet URL hoặc ID.",
                data={},
                entity_type="sheet_profile",
            )

        service = SheetInspectionService(sheets)
        metadata = await service.list_worksheets(spreadsheet_id)
        if arguments.sheet_name is None:
            return ToolResult(
                success=True,
                message=(
                    f"📄 {metadata.title}\nCác tab: "
                    + ", ".join(metadata.worksheet_names)
                    + "\nGọi lại kèm sheet_name để xem mapping đề xuất."
                ),
                data={
                    "spreadsheet_id": spreadsheet_id,
                    "title": metadata.title,
                    "worksheets": metadata.worksheet_names,
                },
                entity_type="sheet_profile",
            )

        inspection = await service.inspect(
            spreadsheet_id=spreadsheet_id,
            sheet_name=arguments.sheet_name,
            header_row=arguments.header_row,
            spreadsheet_title=metadata.title,
            use_llm=False,
        )
        mapping_lines = "\n".join(
            f"  {field} ← {header!r}" for field, header in inspection.proposal.mapping.items()
        )
        return ToolResult(
            success=True,
            message=(
                f"📄 {metadata.title} · tab {arguments.sheet_name!r}\n"
                f"Cột: {', '.join(inspection.headers)}\n"
                f"Mapping đề xuất:\n{mapping_lines}"
            ),
            data={
                "spreadsheet_id": spreadsheet_id,
                "sheet_name": arguments.sheet_name,
                "headers": inspection.headers,
                "mapping": inspection.proposal.mapping,
                "write_back": inspection.write_back,
                "sample_rows": inspection.sample_rows[:INSPECT_SAMPLE_ROWS],
                "fingerprint": inspection.fingerprint,
            },
            entity_type="sheet_profile",
        )

    async def sync_handler(context: ToolContext, arguments: ProfileReferenceArgs) -> ToolResult:
        session = context.require_session()
        audit = AuditService(session)
        profiles = SheetProfileService(session, audit)
        profile = await profiles.get(arguments.profile_id)
        report = await ScriptSyncService(session, audit, sheets, profiles).sync_profile(
            actor=context.actor,
            request_id=context.request_id,
            profile=profile,
        )
        return ToolResult(
            success=not report.failed,
            message=summarize_reports([report]),
            data=dict(report.as_dict()),
            entity_type="sheet_profile",
            entity_id=str(profile.id),
        )

    async def deactivate_handler(
        context: ToolContext, arguments: ProfileReferenceArgs
    ) -> ToolResult:
        session = context.require_session()
        service = SheetProfileService(session, AuditService(session))
        profile = await service.deactivate(
            actor=context.actor,
            request_id=context.request_id,
            profile_id=arguments.profile_id,
        )
        return ToolResult(
            success=True,
            message=f"⏸ Đã tạm dừng đồng bộ sheet profile {profile.name!r}.",
            data={"id": str(profile.id), "active": profile.active},
            entity_type="sheet_profile",
            entity_id=str(profile.id),
        )

    return [
        ToolDefinition(
            name="sheet_profile.list",
            description="Liệt kê các sheet profile đang hoạt động.",
            handler=_list_handler,
            arguments_model=NoArguments,
            risk_level=RiskLevel.LOW,
            required_permission=Permission.SHEET_PROFILE_READ,
            read_only=True,
        ),
        ToolDefinition(
            name="sheet_profile.inspect",
            description="Đọc cấu trúc một Google Sheet và đề xuất mapping cột.",
            handler=inspect_handler,
            arguments_model=InspectSheetArgs,
            risk_level=RiskLevel.LOW,
            required_permission=Permission.SHEET_PROFILE_READ,
            read_only=True,
        ),
        ToolDefinition(
            name="sheet_profile.create",
            description="Tạo một sheet profile mới để MeoBot đọc được Google Sheet.",
            handler=_create_handler,
            arguments_model=CreateSheetProfileArgs,
            risk_level=RiskLevel.MEDIUM,
            required_permission=Permission.SHEET_PROFILE_WRITE,
            read_only=False,
        ),
        ToolDefinition(
            name="sheet_profile.update",
            description="Cập nhật mapping cột của một sheet profile.",
            handler=_update_handler,
            arguments_model=UpdateMappingArgs,
            risk_level=RiskLevel.MEDIUM,
            required_permission=Permission.SHEET_PROFILE_WRITE,
            read_only=False,
        ),
        ToolDefinition(
            name="sheet_profile.sync",
            description="Đồng bộ kịch bản từ một Google Sheet vào MeoBot.",
            handler=sync_handler,
            arguments_model=ProfileReferenceArgs,
            risk_level=RiskLevel.MEDIUM,
            required_permission=Permission.SHEET_PROFILE_WRITE,
            read_only=False,
        ),
        ToolDefinition(
            name="sheet_profile.deactivate",
            description="Tạm dừng đồng bộ một sheet profile (không xoá dữ liệu đã nhập).",
            handler=deactivate_handler,
            arguments_model=ProfileReferenceArgs,
            risk_level=RiskLevel.HIGH,
            required_permission=Permission.SHEET_PROFILE_WRITE,
            read_only=False,
        ),
    ]
