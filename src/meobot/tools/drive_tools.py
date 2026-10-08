"""Drive and spreadsheet-creation tools.

Risk levels follow what a wrong execution would cost:

**LOW** - listing templates, listing allowed folders, inspecting one folder,
listing spreadsheets MeoBot created. All read-only.

**MEDIUM** - registering an allowed folder, creating a work sheet, creating a
script sheet. These change state and cost something to undo by hand, but they
are the daily work; requiring a confirmation token for each would train people
to confirm without reading. The *bot handler* still shows a preview and asks
before enqueuing.

**HIGH** - activating or deactivating a template, and repointing a template at
a different source file. These silently change what every future creation
produces, which is precisely the kind of change somebody should have to
confirm explicitly.

There is no deletion tool, at any risk level. The Drive client has no delete
method for one to call.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field

from meobot.application.audit_service import AuditService
from meobot.application.drive_folder_service import DriveFolderService
from meobot.application.sheet_template_service import SheetTemplateService
from meobot.application.spreadsheet_creation_service import SpreadsheetCreationService
from meobot.core.errors import ValidationError
from meobot.core.time import format_local
from meobot.domain.drive.models import (
    SpreadsheetRequest,
    TemplateKind,
    extract_folder_id,
    folder_url,
    short_id,
)
from meobot.domain.drive.templates import template_for_kind
from meobot.domain.permissions.matrix import Permission
from meobot.domain.policy.models import RiskLevel
from meobot.integrations.google.drive import DriveClient
from meobot.integrations.google.sheets import SheetsClient
from meobot.tools.base import NoArguments, ToolContext, ToolDefinition, ToolResult

#: Subfolders listed when inspecting a folder. Bounded on purpose: this is a
#: convenience for finding a destination, not a Drive browser.
MAX_CHILD_FOLDERS = 20


class FolderReferenceArgs(BaseModel):
    """Arguments for tools addressing one Drive folder."""

    model_config = ConfigDict(extra="forbid")

    folder: str = Field(min_length=1, max_length=500, description="Link hoặc ID thư mục Drive.")


class RegisterFolderArgs(BaseModel):
    """Arguments for ``drive.folder.register``."""

    model_config = ConfigDict(extra="forbid")

    folder: str = Field(min_length=1, max_length=500)
    path_label: str | None = Field(default=None, max_length=500)
    purpose: str | None = Field(default=None, max_length=300)
    team_scope: str | None = Field(default=None, max_length=100)


class CreateSpreadsheetArgs(BaseModel):
    """Arguments shared by both creation tools."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=300, description="Tên file sẽ tạo.")
    folder: str = Field(
        min_length=1,
        max_length=500,
        description="Thư mục đích: tên, mã, hoặc ID thư mục ĐÃ ĐĂNG KÝ với TasksBot.",
    )
    period: str | None = Field(default=None, max_length=100, description="Kỳ / tháng.")
    team: str | None = Field(default=None, max_length=100)
    channel: str | None = Field(default=None, max_length=100)
    campaign: str | None = Field(default=None, max_length=200)


class CreatedReferenceArgs(BaseModel):
    """Arguments for ``spreadsheet.get_created``."""

    model_config = ConfigDict(extra="forbid")

    created_id: uuid.UUID


class TemplateToggleArgs(BaseModel):
    """Arguments for ``sheet_template.set_active``."""

    model_config = ConfigDict(extra="forbid")

    template_id: uuid.UUID
    active: bool


class TemplateSourceArgs(BaseModel):
    """Arguments for ``sheet_template.set_source``."""

    model_config = ConfigDict(extra="forbid")

    template_id: uuid.UUID
    source_file_id: str | None = Field(default=None, max_length=200)


def build_drive_tools(*, drive: DriveClient, sheets: SheetsClient) -> list[ToolDefinition]:
    """Tools for Drive folders, sheet templates and spreadsheet creation."""

    async def list_folders(context: ToolContext, arguments: NoArguments) -> ToolResult:
        session = context.require_session()
        service = DriveFolderService(session, AuditService(session), drive, context.settings)
        folders = list(await service.list_folders(active_only=True))
        if not folders:
            return ToolResult(
                success=True,
                message=(
                    "Chưa có thư mục Drive nào được đăng ký. "
                    "OWNER/ADMIN dùng /add_drive_folder để thêm."
                ),
                data={"items": []},
                entity_type="drive_folder",
            )
        lines = ["📁 Thư mục Drive TasksBot được phép tạo file:"]
        for folder in folders:
            marks = []
            if folder.shared_drive_id:
                marks.append("Shared Drive")
            if folder.team_scope:
                marks.append(f"team {folder.team_scope}")
            if folder.validation_status != "valid":
                marks.append(f"⚠️ {folder.validation_status}")
            suffix = f" ({', '.join(marks)})" if marks else ""
            lines.append(f"• {folder.name}{suffix} — {folder.purpose or 'chưa ghi mục đích'}")
        return ToolResult(
            success=True,
            message="\n".join(lines),
            data={
                "items": [
                    {
                        "id": str(folder.id),
                        "name": folder.name,
                        "drive_folder_id": folder.drive_folder_id,
                        "shared_drive_id": folder.shared_drive_id,
                        "path_label": folder.path_label,
                        "purpose": folder.purpose,
                        "team_scope": folder.team_scope,
                        "validation_status": folder.validation_status,
                    }
                    for folder in folders
                ]
            },
            entity_type="drive_folder",
        )

    async def inspect_folder(context: ToolContext, arguments: FolderReferenceArgs) -> ToolResult:
        folder_id = extract_folder_id(arguments.folder)
        if folder_id is None:
            return ToolResult(
                success=False,
                message="Không nhận ra link hoặc ID thư mục Drive.",
                entity_type="drive_folder",
            )
        session = context.require_session()
        service = DriveFolderService(session, AuditService(session), drive, context.settings)
        validation = await service.validate_folder_id(folder_id)
        file = validation.file
        children = []
        if validation.ok and file is not None:
            children = [
                child.name
                for child in await drive.list_child_folders(folder_id, limit=MAX_CHILD_FOLDERS)
            ]

        lines = [f"📁 {file.name if file else short_id(folder_id)}"]
        lines.append(f"Trạng thái: {validation.status.value}")
        if validation.message:
            lines.append(validation.message)
        if file is not None and file.drive_id:
            lines.append("Nằm trong Shared Drive ✅")
        if children:
            lines.append("Thư mục con: " + ", ".join(children))
        return ToolResult(
            success=validation.ok,
            message="\n".join(lines),
            data={
                "folder_id": folder_id,
                "name": file.name if file else None,
                "shared_drive_id": file.drive_id if file else None,
                "validation_status": validation.status.value,
                "child_folders": children,
                "url": folder_url(folder_id),
            },
            entity_type="drive_folder",
            entity_id=folder_id,
        )

    async def register_folder(context: ToolContext, arguments: RegisterFolderArgs) -> ToolResult:
        folder_id = extract_folder_id(arguments.folder)
        if folder_id is None:
            raise ValidationError("Không nhận ra link hoặc ID thư mục Drive.")
        session = context.require_session()
        service = DriveFolderService(session, AuditService(session), drive, context.settings)
        folder = await service.register(
            actor=context.actor,
            request_id=context.request_id,
            drive_folder_id=folder_id,
            path_label=arguments.path_label,
            purpose=arguments.purpose,
            team_scope=arguments.team_scope,
        )
        return ToolResult(
            success=True,
            message=(
                f"✅ Đã đăng ký thư mục {folder.name!r}"
                + (" (Shared Drive)" if folder.shared_drive_id else "")
                + "."
            ),
            data={"id": str(folder.id), "name": folder.name},
            entity_type="drive_folder",
            entity_id=str(folder.id),
        )

    async def list_templates(context: ToolContext, arguments: NoArguments) -> ToolResult:
        session = context.require_session()
        service = SheetTemplateService(session, AuditService(session), context.settings)
        await service.ensure_all_builtin()
        templates = list(await service.list_templates(active_only=True))
        lines = ["🧩 Mẫu Sheet TasksBot tạo được:"]
        for template in templates:
            method = (
                "sao chép từ mẫu có sẵn" if template.source_file_id else "tạo Sheet trắng chuẩn"
            )
            tabs = ", ".join(template.expected_tabs.get("tabs", []))
            lines.append(f"• {template.name} (v{template.version}) — {method}\n  tab: {tabs}")
        return ToolResult(
            success=True,
            message="\n".join(lines),
            data={
                "items": [
                    {
                        "id": str(template.id),
                        "code": template.code,
                        "name": template.name,
                        "kind": template.kind,
                        "version": template.version,
                        "has_source_file": bool(template.source_file_id),
                        "tabs": template.expected_tabs.get("tabs", []),
                        "worksheet": template.default_worksheet_name,
                    }
                    for template in templates
                ]
            },
            entity_type="sheet_template",
        )

    async def _create(
        context: ToolContext,
        arguments: CreateSpreadsheetArgs,
        kind: TemplateKind,
    ) -> ToolResult:
        session = context.require_session()
        audit = AuditService(session)
        folders = DriveFolderService(session, audit, drive, context.settings)
        folder = await folders.resolve_destination(arguments.folder)
        folders.assert_usable_by(folder, context.actor)

        spec = template_for_kind(kind)
        templates = SheetTemplateService(session, audit, context.settings)
        template = await templates.for_kind(kind)

        request = SpreadsheetRequest(
            template_code=template.code,
            template_version=template.version,
            name=arguments.name,
            folder_id=folder.drive_folder_id,
            kind=kind,
            team=arguments.team,
            channel=arguments.channel,
            campaign=arguments.campaign,
            period=arguments.period,
            actor_reference=str(context.actor.telegram_user_id or context.actor.user_id or ""),
            confirmation_reference=str(context.request_id),
            register_profile=kind is TemplateKind.SCRIPT_MANAGEMENT,
            run_initial_sync=False,
        )
        service = SpreadsheetCreationService(session, audit, drive, sheets, context.settings)
        outcome = await service.create(
            actor=context.actor, request_id=context.request_id, request=request
        )

        method = (
            "sao chép từ mẫu"
            if outcome.method and outcome.method.value == "template_copy"
            else "Sheet trắng chuẩn"
        )
        lines = [
            ("✅ Đã tạo" if outcome.created_now else "♻️ Sheet này đã được tạo trước đó")
            + f" {outcome.record.name!r} ({method}).",
            f"Thư mục: {folder.name}",
            f"Tab: {', '.join(spec.expected_tabs)}",
        ]
        if outcome.url:
            lines.append(f"Link: {outcome.url}")
        if outcome.profile_id is not None:
            lines.append(f"Đã đăng ký Sheet Profile: {outcome.profile_id}")
            lines.append("Đồng bộ ngay: /sync_sheets")
        elif kind is TemplateKind.WORK_MANAGEMENT:
            lines.append("Sheet công việc không đăng ký làm nguồn kịch bản.")

        return ToolResult(
            success=outcome.succeeded,
            message="\n".join(lines),
            data={
                "id": str(outcome.record.id),
                "name": outcome.record.name,
                "kind": outcome.record.kind,
                "spreadsheet_id": outcome.record.spreadsheet_id,
                "spreadsheet_url": outcome.record.spreadsheet_url,
                "creation_method": outcome.record.creation_method,
                "created_now": outcome.created_now,
                "sheet_profile_id": str(outcome.profile_id) if outcome.profile_id else None,
            },
            entity_type="created_spreadsheet",
            entity_id=str(outcome.record.id),
        )

    async def create_work(context: ToolContext, arguments: CreateSpreadsheetArgs) -> ToolResult:
        return await _create(context, arguments, TemplateKind.WORK_MANAGEMENT)

    async def create_script(context: ToolContext, arguments: CreateSpreadsheetArgs) -> ToolResult:
        return await _create(context, arguments, TemplateKind.SCRIPT_MANAGEMENT)

    async def list_created(context: ToolContext, arguments: NoArguments) -> ToolResult:
        session = context.require_session()
        service = SpreadsheetCreationService(
            session, AuditService(session), drive, sheets, context.settings
        )
        records = await service.list_created()
        if not records:
            return ToolResult(
                success=True,
                message="TasksBot chưa tạo Sheet nào.",
                data={"items": []},
                entity_type="created_spreadsheet",
            )
        timezone = context.settings.timezone
        lines = ["📄 Sheet TasksBot đã tạo:"]
        for record in records:
            when = (
                format_local(record.created_at, timezone, "%d/%m %H:%M")
                if record.created_at
                else "—"
            )
            link = record.spreadsheet_url or "chưa có link"
            lines.append(f"• {record.name} ({record.kind}) — {when}\n  {link}")
        return ToolResult(
            success=True,
            message="\n".join(lines),
            data={"items": [_created_payload(record) for record in records]},
            entity_type="created_spreadsheet",
        )

    async def get_created(context: ToolContext, arguments: CreatedReferenceArgs) -> ToolResult:
        session = context.require_session()
        service = SpreadsheetCreationService(
            session, AuditService(session), drive, sheets, context.settings
        )
        record = await service.get(arguments.created_id)
        return ToolResult(
            success=True,
            message=(
                f"📄 {record.name} ({record.kind})\n"
                f"Trạng thái: {record.creation_status}\n"
                f"{record.spreadsheet_url or 'chưa có link'}"
            ),
            data=_created_payload(record),
            entity_type="created_spreadsheet",
            entity_id=str(record.id),
        )

    async def set_template_active(
        context: ToolContext, arguments: TemplateToggleArgs
    ) -> ToolResult:
        session = context.require_session()
        service = SheetTemplateService(session, AuditService(session), context.settings)
        template = await service.set_active(
            actor=context.actor,
            request_id=context.request_id,
            template_id=arguments.template_id,
            active=arguments.active,
        )
        state = "bật" if template.active else "tắt"
        return ToolResult(
            success=True,
            message=f"Đã {state} mẫu {template.name!r}.",
            data={"id": str(template.id), "active": template.active},
            entity_type="sheet_template",
            entity_id=str(template.id),
        )

    async def set_template_source(
        context: ToolContext, arguments: TemplateSourceArgs
    ) -> ToolResult:
        session = context.require_session()
        service = SheetTemplateService(session, AuditService(session), context.settings)
        template = await service.set_source_file(
            actor=context.actor,
            request_id=context.request_id,
            template_id=arguments.template_id,
            source_file_id=arguments.source_file_id,
        )
        return ToolResult(
            success=True,
            message=(
                f"Đã đổi file mẫu của {template.name!r} thành {short_id(template.source_file_id)}."
                if template.source_file_id
                else f"Đã bỏ file mẫu của {template.name!r}; TasksBot sẽ tạo Sheet trắng chuẩn."
            ),
            data={"id": str(template.id), "source_file_id": template.source_file_id},
            entity_type="sheet_template",
            entity_id=str(template.id),
        )

    return [
        ToolDefinition(
            name="drive.folder.list",
            description="Liệt kê các thư mục Google Drive TasksBot được phép tạo file.",
            handler=list_folders,
            arguments_model=NoArguments,
            risk_level=RiskLevel.LOW,
            required_permission=Permission.DRIVE_FOLDER_READ,
            read_only=True,
        ),
        ToolDefinition(
            name="drive.folder.inspect",
            description="Kiểm tra một thư mục Drive: tồn tại, quyền, Shared Drive.",
            handler=inspect_folder,
            arguments_model=FolderReferenceArgs,
            risk_level=RiskLevel.LOW,
            required_permission=Permission.DRIVE_FOLDER_READ,
            read_only=True,
        ),
        ToolDefinition(
            name="drive.folder.register",
            description="Đăng ký một thư mục Drive làm nơi TasksBot được phép tạo file.",
            handler=register_folder,
            arguments_model=RegisterFolderArgs,
            risk_level=RiskLevel.MEDIUM,
            required_permission=Permission.DRIVE_FOLDER_MANAGE,
            read_only=False,
        ),
        ToolDefinition(
            name="sheet_template.list",
            description="Liệt kê các mẫu Sheet TasksBot có thể tạo.",
            handler=list_templates,
            arguments_model=NoArguments,
            risk_level=RiskLevel.LOW,
            required_permission=Permission.SHEET_TEMPLATE_READ,
            read_only=True,
        ),
        ToolDefinition(
            name="spreadsheet.create_work",
            description="Tạo một Google Sheet quản lý công việc từ mẫu chuẩn.",
            handler=create_work,
            arguments_model=CreateSpreadsheetArgs,
            risk_level=RiskLevel.MEDIUM,
            required_permission=Permission.SPREADSHEET_CREATE,
            read_only=False,
        ),
        ToolDefinition(
            name="spreadsheet.create_script",
            description=(
                "Tạo một Google Sheet quản lý kịch bản và đăng ký nó làm nguồn kịch bản "
                "cho TasksBot."
            ),
            handler=create_script,
            arguments_model=CreateSpreadsheetArgs,
            risk_level=RiskLevel.MEDIUM,
            required_permission=Permission.SPREADSHEET_CREATE,
            read_only=False,
        ),
        ToolDefinition(
            name="spreadsheet.list_created",
            description="Liệt kê các Google Sheet mà TasksBot đã tạo.",
            handler=list_created,
            arguments_model=NoArguments,
            risk_level=RiskLevel.LOW,
            required_permission=Permission.SPREADSHEET_READ,
            read_only=True,
        ),
        ToolDefinition(
            name="spreadsheet.get_created",
            description="Xem chi tiết một Google Sheet mà TasksBot đã tạo.",
            handler=get_created,
            arguments_model=CreatedReferenceArgs,
            risk_level=RiskLevel.LOW,
            required_permission=Permission.SPREADSHEET_READ,
            read_only=True,
        ),
        ToolDefinition(
            name="sheet_template.set_active",
            description="Bật hoặc tắt một mẫu Sheet (ảnh hưởng mọi lần tạo sau đó).",
            handler=set_template_active,
            arguments_model=TemplateToggleArgs,
            risk_level=RiskLevel.HIGH,
            required_permission=Permission.SHEET_TEMPLATE_MANAGE,
            read_only=False,
        ),
        ToolDefinition(
            name="sheet_template.set_source",
            description="Đổi file Google Sheet mẫu mà một template sao chép từ đó.",
            handler=set_template_source,
            arguments_model=TemplateSourceArgs,
            risk_level=RiskLevel.HIGH,
            required_permission=Permission.SHEET_TEMPLATE_MANAGE,
            read_only=False,
        ),
    ]


def _created_payload(record: object) -> dict[str, object]:
    """JSON view of one ``created_spreadsheets`` row."""
    from meobot.db.models.drive import CreatedSpreadsheet

    assert isinstance(record, CreatedSpreadsheet)
    return {
        "id": str(record.id),
        "name": record.name,
        "kind": record.kind,
        "template_code": record.template_code,
        "template_version": record.template_version,
        "spreadsheet_id": record.spreadsheet_id,
        "spreadsheet_url": record.spreadsheet_url,
        "parent_folder_id": record.parent_folder_id,
        "shared_drive_id": record.shared_drive_id,
        "creation_method": record.creation_method,
        "creation_status": record.creation_status,
        "sheet_profile_id": str(record.sheet_profile_id) if record.sheet_profile_id else None,
        "created_by_telegram_id": record.created_by_telegram_id,
        "created_at": record.created_at.isoformat() if record.created_at else None,
    }
