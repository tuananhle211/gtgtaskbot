"""Drive commands and the guided Sheet-creation conversations.

Three flows live here, all built the same way: collect answers into an aiogram
FSM (backed by PostgreSQL, so a restart mid-flow loses nothing), show a preview
of what will actually happen, ask for confirmation, then hand the work to
Celery and acknowledge immediately.

The preview is not decoration. Creating a file in a team's Drive is not
reversible by MeoBot - there is no delete tool - so the destination folder, the
template, the tab layout and whether a Sheet Profile will be registered are all
shown before anything is created.

Handlers stay transport-only: they parse input, call an application service or
enqueue a task, and format the reply.
"""

from __future__ import annotations

import uuid

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from meobot.application.audit_service import AuditService
from meobot.application.drive_folder_service import DriveFolderService
from meobot.application.sheet_template_service import SheetTemplateService
from meobot.application.spreadsheet_creation_service import SpreadsheetCreationService
from meobot.bot import formatting
from meobot.core.config import Settings
from meobot.core.errors import MeoBotError
from meobot.core.logging import get_logger
from meobot.core.time import format_local
from meobot.db.session import Database
from meobot.domain.drive.models import (
    SpreadsheetRequest,
    TemplateKind,
    extract_folder_id,
    short_id,
)
from meobot.domain.drive.templates import template_for_kind
from meobot.domain.identity.models import Actor
from meobot.domain.permissions.matrix import Permission, has_permission
from meobot.integrations.google.drive import DriveClient
from meobot.integrations.google.sheets import SheetsClient
from meobot.tasks.drive import create_script_spreadsheet, create_work_spreadsheet

logger = get_logger(__name__)

router = Router(name="drive")

#: Destination folders offered as buttons; beyond this the user types a name.
MAX_FOLDER_BUTTONS = 10

SKIP_TOKENS: frozenset[str] = frozenset({"-", "bo qua", "bỏ qua", "khong", "không", "skip"})


class AddDriveFolder(StatesGroup):
    """Steps of ``/add_drive_folder``."""

    waiting_for_folder = State()
    waiting_for_label = State()
    waiting_for_purpose = State()
    waiting_for_team = State()
    waiting_for_confirmation = State()


class CreateSheet(StatesGroup):
    """Steps shared by ``/create_work_sheet`` and ``/create_script_sheet``."""

    waiting_for_name = State()
    waiting_for_period = State()
    waiting_for_team = State()
    waiting_for_channel = State()
    waiting_for_campaign = State()
    waiting_for_folder = State()
    waiting_for_confirmation = State()


def _require(actor: Actor, permission: Permission) -> bool:
    return has_permission(actor.role, permission)


def _optional(text: str) -> str | None:
    """Treat the skip tokens as "not answered"."""
    cleaned = text.strip()
    return None if not cleaned or cleaned.lower() in SKIP_TOKENS else cleaned


# --- Status and listings ---------------------------------------------------
@router.message(Command("drive_status"))
async def handle_drive_status(
    message: Message,
    actor: Actor,
    database: Database,
    settings: Settings,
    drive_client: DriveClient,
) -> None:
    """Report what Drive configuration exists and whether Drive answers.

    Ids are shortened rather than printed in full: they are not secrets, but a
    full id in a chat log invites pasting into the wrong place.
    """
    if not _require(actor, Permission.DRIVE_FOLDER_READ):
        await formatting.answer(message, "⛔ Bạn không có quyền xem cấu hình Google Drive.")
        return

    reachable = "chưa kiểm tra"
    if settings.google_enabled:
        root = settings.google_drive_root_folder_id or settings.google_shared_drive_id
        if root:
            try:
                await drive_client.get_file(root)
                reachable = "✅ kết nối được"
            except MeoBotError as exc:
                reachable = f"⚠️ {exc.message}"
        else:
            reachable = "chưa cấu hình thư mục gốc để kiểm tra"

    async with database.session() as session:
        folders = await DriveFolderService(
            session, AuditService(session), drive_client, settings
        ).list_folders(active_only=True)
        usable = sum(1 for folder in folders if folder.validation_status == "valid")

    def _template_line(template_id: str | None) -> str:
        """Show the shortened id, or say what happens without one."""
        if not template_id:
            return "— sẽ tạo Sheet trắng chuẩn"
        return "✅ " + short_id(template_id)

    credentials = "✅ đã cấu hình" if settings.google_enabled else "❌ chưa cấu hình"
    lines = [
        "💽 " + formatting.bold("Google Drive"),
        f"Thông tin đăng nhập Google: {credentials}",
        f"Kết nối Drive: {formatting.escape(reachable)}",
        f"Thư mục gốc: {formatting.escape(short_id(settings.google_drive_root_folder_id))}",
        f"Shared Drive: {formatting.escape(short_id(settings.google_shared_drive_id))}",
        "Mẫu Sheet công việc: "
        + formatting.escape(_template_line(settings.google_work_sheet_template_id)),
        "Mẫu Sheet kịch bản: "
        + formatting.escape(_template_line(settings.google_script_sheet_template_id)),
        f"Thư mục được phép: {len(folders)} (dùng được: {usable})",
    ]
    if not settings.google_enabled:
        lines.append("")
        lines.append(
            formatting.escape(
                "Cần đặt GOOGLE_SERVICE_ACCOUNT_FILE và mount file service account. "
                "Các chức năng khác của MeoBot vẫn chạy bình thường."
            )
        )
    await formatting.answer(message, "\n".join(lines))


@router.message(Command("sheet_templates"))
async def handle_sheet_templates(
    message: Message,
    actor: Actor,
    database: Database,
    settings: Settings,
) -> None:
    """List the templates MeoBot can produce, and how it would produce them."""
    if not _require(actor, Permission.SHEET_TEMPLATE_READ):
        await formatting.answer(message, "⛔ Bạn không có quyền xem mẫu Sheet.")
        return

    async with database.transaction() as session:
        service = SheetTemplateService(session, AuditService(session), settings)
        await service.ensure_all_builtin()
        templates = list(await service.list_templates(active_only=False))

    lines = ["🧩 " + formatting.bold("Mẫu Sheet MeoBot tạo được")]
    for template in templates:
        method = (
            "sao chép mẫu có sẵn định dạng"
            if template.source_file_id
            else "tạo Sheet trắng theo chuẩn"
        )
        tabs = ", ".join(template.expected_tabs.get("tabs", []))
        state = "" if template.active else " (đang tắt)"
        lines.append("")
        lines.append(
            f"• {formatting.bold(template.name)} v{template.version}{formatting.escape(state)}"
        )
        lines.append(f"  {formatting.escape(template.description or '')}")
        lines.append(f"  Cách tạo: {formatting.escape(method)}")
        lines.append(f"  Tab: {formatting.escape(tabs)}")
    lines.append("")
    lines.append(formatting.escape("Tạo Sheet: /create_work_sheet hoặc /create_script_sheet"))
    await formatting.answer(message, "\n".join(lines))


@router.message(Command("drive_folders"))
async def handle_drive_folders(
    message: Message,
    actor: Actor,
    database: Database,
    settings: Settings,
    drive_client: DriveClient,
) -> None:
    """List the folders MeoBot is allowed to create files in."""
    if not _require(actor, Permission.DRIVE_FOLDER_READ):
        await formatting.answer(message, "⛔ Bạn không có quyền xem thư mục Drive.")
        return

    async with database.session() as session:
        folders = list(
            await DriveFolderService(
                session, AuditService(session), drive_client, settings
            ).list_folders(active_only=False)
        )

    if not folders:
        await formatting.answer(
            message,
            "Chưa có thư mục Drive nào được đăng ký.\n"
            "OWNER/ADMIN dùng /add_drive_folder để thêm một thư mục.",
        )
        return

    lines = ["📁 " + formatting.bold("Thư mục Drive được phép")]
    for folder in folders:
        marks: list[str] = []
        if folder.shared_drive_id:
            marks.append("Shared Drive")
        if folder.team_scope:
            marks.append(f"team {folder.team_scope}")
        if not folder.active:
            marks.append("đang tắt")
        if folder.validation_status != "valid":
            marks.append(f"⚠️ {folder.validation_status}")
        lines.append("")
        lines.append(f"• {formatting.bold(folder.name)}")
        if marks:
            lines.append("  " + formatting.escape(" · ".join(marks)))
        if folder.path_label:
            lines.append("  " + formatting.escape(folder.path_label))
        if folder.purpose:
            lines.append("  Mục đích: " + formatting.escape(folder.purpose))
    await formatting.answer(message, "\n".join(lines))


@router.message(Command("created_sheets"))
async def handle_created_sheets(
    message: Message,
    actor: Actor,
    database: Database,
    settings: Settings,
    drive_client: DriveClient,
    sheets_client: SheetsClient,
) -> None:
    """List the spreadsheets MeoBot has created."""
    if not _require(actor, Permission.SPREADSHEET_READ):
        await formatting.answer(message, "⛔ Bạn không có quyền xem danh sách Sheet đã tạo.")
        return

    async with database.session() as session:
        records = await SpreadsheetCreationService(
            session,
            AuditService(session),
            drive_client,
            sheets_client,
            settings,
        ).list_created()

    if not records:
        await formatting.answer(message, "MeoBot chưa tạo Sheet nào.")
        return

    lines = ["📄 " + formatting.bold("Sheet MeoBot đã tạo")]
    for record in records:
        when = (
            format_local(record.created_at, settings.timezone, "%d/%m/%Y %H:%M")
            if record.created_at
            else "—"
        )
        lines.append("")
        lines.append(f"• {formatting.bold(record.name)} ({formatting.escape(record.kind)})")
        status = formatting.escape(record.creation_status)
        lines.append(f"  Tạo lúc: {formatting.escape(when)} · trạng thái: {status}")
        if record.created_by_telegram_id:
            lines.append(f"  Người tạo: {record.created_by_telegram_id}")
        if record.sheet_profile_id:
            lines.append(f"  Sheet Profile: {formatting.code(record.sheet_profile_id)}")
        if record.spreadsheet_url:
            lines.append("  " + formatting.link(record.spreadsheet_url, "Mở Sheet"))
    await formatting.answer(message, "\n".join(lines))


# --- /add_drive_folder -----------------------------------------------------
@router.message(Command("add_drive_folder"))
async def handle_add_drive_folder(
    message: Message,
    actor: Actor,
    state: FSMContext,
    settings: Settings,
) -> None:
    """Start the guided folder-registration conversation."""
    if not _require(actor, Permission.DRIVE_FOLDER_MANAGE):
        await formatting.answer(message, "⛔ Chỉ OWNER hoặc ADMIN mới đăng ký được thư mục Drive.")
        return
    if not settings.google_enabled:
        await formatting.answer(
            message,
            "⚠️ Google chưa được cấu hình.\n"
            "Cần đặt GOOGLE_SERVICE_ACCOUNT_FILE và mount file service account trước.",
        )
        return

    await state.clear()
    await state.set_state(AddDriveFolder.waiting_for_folder)
    await formatting.answer(
        message,
        "📁 Gửi link thư mục Google Drive (hoặc folder ID).\n\n"
        "Nhớ chia sẻ thư mục đó với email service account của MeoBot, "
        "quyền Content manager (hoặc Editor).\n"
        "Gõ /cancel_flow để huỷ.",
    )


@router.message(AddDriveFolder.waiting_for_folder, F.text)
async def handle_folder_input(
    message: Message,
    state: FSMContext,
    settings: Settings,
    database: Database,
    drive_client: DriveClient,
) -> None:
    """Validate the folder against Google before asking anything else."""
    folder_id = extract_folder_id(message.text or "")
    if folder_id is None:
        await formatting.answer(
            message,
            "Không nhận ra link. Gửi dạng "
            "https://drive.google.com/drive/folders/&lt;ID&gt; hoặc chính ID đó.",
        )
        return

    async with formatting.typing(message, enabled=settings.chat_typing_indicator):
        await formatting.answer(message, "⏳ Đang kiểm tra thư mục với Google...")
        async with database.session() as session:
            service = DriveFolderService(session, AuditService(session), drive_client, settings)
            validation = await service.validate_folder_id(folder_id)

    if not validation.ok:
        await state.clear()
        await formatting.answer(
            message,
            "⛔ "
            + formatting.escape(validation.message or validation.status.value)
            + "\n\nGõ /add_drive_folder để thử lại với thư mục khác.",
        )
        return

    assert validation.file is not None
    await state.update_data(
        folder_id=folder_id,
        folder_name=validation.file.name,
        shared_drive_id=validation.file.drive_id,
    )
    await state.set_state(AddDriveFolder.waiting_for_label)
    await formatting.answer(
        message,
        f"✅ Tìm thấy thư mục {formatting.bold(validation.file.name)}"
        + (" (nằm trong Shared Drive)" if validation.file.drive_id else " (nằm trong My Drive)")
        + ".\n\nĐặt nhãn đường dẫn cho dễ nhớ, ví dụ "
        + formatting.code("Marketing / Kịch bản / TikTok")
        + ".\nGửi "
        + formatting.code("-")
        + " để bỏ qua.",
    )


@router.message(AddDriveFolder.waiting_for_label, F.text)
async def handle_folder_label(message: Message, state: FSMContext) -> None:
    """Record the path label and ask what the folder is for."""
    await state.update_data(path_label=_optional(message.text or ""))
    await state.set_state(AddDriveFolder.waiting_for_purpose)
    await formatting.answer(
        message,
        "Thư mục này dùng để làm gì? Ví dụ "
        + formatting.code("Sheet kịch bản TikTok của bác sĩ")
        + ".\nGửi "
        + formatting.code("-")
        + " để bỏ qua.",
    )


@router.message(AddDriveFolder.waiting_for_purpose, F.text)
async def handle_folder_purpose(message: Message, state: FSMContext) -> None:
    """Record the purpose and ask about team scoping."""
    await state.update_data(purpose=_optional(message.text or ""))
    await state.set_state(AddDriveFolder.waiting_for_team)
    await formatting.answer(
        message,
        "Giới hạn thư mục này cho một team cụ thể? Gửi tên team, "
        "hoặc "
        + formatting.code("-")
        + " nếu dùng chung.\n\n"
        + formatting.escape(
            "Lưu ý: thư mục có giới hạn team chỉ ADMIN/OWNER tạo file được, "
            "vì MeoBot chưa quản lý ai thuộc team nào."
        ),
    )


@router.message(AddDriveFolder.waiting_for_team, F.text)
async def handle_folder_team(message: Message, state: FSMContext) -> None:
    """Show the summary and ask for confirmation."""
    await state.update_data(team_scope=_optional(message.text or ""))
    data = await state.get_data()
    await state.set_state(AddDriveFolder.waiting_for_confirmation)

    lines = [
        "📋 " + formatting.bold("Xác nhận đăng ký thư mục"),
        f"Tên: {formatting.escape(data.get('folder_name'))}",
        f"ID: {formatting.code(short_id(str(data.get('folder_id'))))}",
        f"Shared Drive: {'có' if data.get('shared_drive_id') else 'không'}",
        f"Nhãn: {formatting.escape(data.get('path_label') or '—')}",
        f"Mục đích: {formatting.escape(data.get('purpose') or '—')}",
        f"Giới hạn team: {formatting.escape(data.get('team_scope') or 'không')}",
        "",
        "Đăng ký thư mục này?",
    ]
    await formatting.answer(
        message,
        "\n".join(lines),
        reply_markup=formatting.keyboard(
            [[("✅ Đăng ký", "dfolder:ok"), ("❌ Huỷ", "dfolder:cancel")]]
        ),
    )


@router.callback_query(AddDriveFolder.waiting_for_confirmation, F.data == "dfolder:ok")
async def handle_folder_confirmed(
    query: CallbackQuery,
    actor: Actor,
    state: FSMContext,
    database: Database,
    settings: Settings,
    drive_client: DriveClient,
    request_id: uuid.UUID,
) -> None:
    """Persist the folder and write an audit entry."""
    await query.answer()
    data = await state.get_data()
    await state.clear()

    try:
        async with database.transaction() as session:
            service = DriveFolderService(session, AuditService(session), drive_client, settings)
            folder = await service.register(
                actor=actor,
                request_id=request_id,
                drive_folder_id=str(data["folder_id"]),
                path_label=data.get("path_label"),
                purpose=data.get("purpose"),
                team_scope=data.get("team_scope"),
            )
            name, folder_uuid = folder.name, folder.id
    except MeoBotError as exc:
        await formatting.answer_callback(query, "⛔ " + formatting.escape(exc.message))
        return

    await formatting.answer_callback(
        query,
        f"✅ Đã đăng ký thư mục {formatting.bold(name)}.\n"
        f"Mã: {formatting.code(folder_uuid)}\n\n"
        "Giờ bạn có thể dùng /create_work_sheet hoặc /create_script_sheet với thư mục này.",
    )


@router.callback_query(AddDriveFolder.waiting_for_confirmation, F.data == "dfolder:cancel")
async def handle_folder_cancelled(query: CallbackQuery, state: FSMContext) -> None:
    """Abandon the registration flow."""
    await query.answer()
    await state.clear()
    await formatting.edit_callback(query, "Đã huỷ đăng ký thư mục.")


# --- /create_work_sheet and /create_script_sheet ---------------------------
@router.message(Command("create_work_sheet"))
async def handle_create_work_sheet(
    message: Message,
    actor: Actor,
    state: FSMContext,
    settings: Settings,
) -> None:
    """Start the guided work-management Sheet creation."""
    await _start_creation(message, actor, state, settings, TemplateKind.WORK_MANAGEMENT)


@router.message(Command("create_script_sheet"))
async def handle_create_script_sheet(
    message: Message,
    actor: Actor,
    state: FSMContext,
    settings: Settings,
) -> None:
    """Start the guided script-management Sheet creation."""
    await _start_creation(message, actor, state, settings, TemplateKind.SCRIPT_MANAGEMENT)


async def _start_creation(
    message: Message,
    actor: Actor,
    state: FSMContext,
    settings: Settings,
    kind: TemplateKind,
) -> None:
    if not _require(actor, Permission.SPREADSHEET_CREATE):
        await formatting.answer(message, "⛔ Bạn không có quyền tạo Google Sheet mới.")
        return
    if not settings.google_enabled:
        await formatting.answer(
            message,
            "⚠️ Google chưa được cấu hình, MeoBot chưa tạo được Sheet.\n"
            "Cần đặt GOOGLE_SERVICE_ACCOUNT_FILE trước.",
        )
        return

    await state.clear()
    await state.update_data(kind=kind.value)
    await state.set_state(CreateSheet.waiting_for_name)
    label = "công việc" if kind is TemplateKind.WORK_MANAGEMENT else "kịch bản"
    await formatting.answer(
        message,
        f"📄 Đặt tên cho Sheet {label} mới.\n"
        "Ví dụ: " + formatting.code("Kịch bản TikTok — BS Tiến — Tháng 8") + "\n"
        "Gõ /cancel_flow để huỷ.",
    )


@router.message(CreateSheet.waiting_for_name, F.text)
async def handle_creation_name(message: Message, state: FSMContext) -> None:
    """Record the file name and ask for the period."""
    name = (message.text or "").strip()
    if not name:
        await formatting.answer(message, "Tên file không được để trống. Gửi lại giúp mình nhé.")
        return
    await state.update_data(name=name[:300])
    await state.set_state(CreateSheet.waiting_for_period)
    await formatting.answer(
        message,
        "Kỳ hoặc tháng áp dụng? Ví dụ " + formatting.code("Tháng 8/2026") + ".\n"
        "Gửi " + formatting.code("-") + " để bỏ qua.",
    )


@router.message(CreateSheet.waiting_for_period, F.text)
async def handle_creation_period(message: Message, state: FSMContext) -> None:
    """Record the period, then branch by kind."""
    await state.update_data(period=_optional(message.text or ""))
    data = await state.get_data()
    if data.get("kind") == TemplateKind.WORK_MANAGEMENT.value:
        await state.set_state(CreateSheet.waiting_for_team)
        await formatting.answer(
            message,
            "Team nào phụ trách? Ví dụ " + formatting.code("Content") + ".\n"
            "Gửi " + formatting.code("-") + " để bỏ qua.",
        )
        return
    await state.set_state(CreateSheet.waiting_for_channel)
    await formatting.answer(
        message,
        "Kênh nội dung là gì? Ví dụ " + formatting.code("TikTok bác sĩ Tiến") + ".\n"
        "Gửi " + formatting.code("-") + " để bỏ qua.",
    )


@router.message(CreateSheet.waiting_for_team, F.text)
async def handle_creation_team(
    message: Message,
    state: FSMContext,
    database: Database,
    settings: Settings,
    drive_client: DriveClient,
) -> None:
    """Record the team (work sheets) and ask for the destination folder."""
    await state.update_data(team=_optional(message.text or ""))
    await _ask_for_folder(message, state, database, settings, drive_client)


@router.message(CreateSheet.waiting_for_channel, F.text)
async def handle_creation_channel(message: Message, state: FSMContext) -> None:
    """Record the channel (script sheets) and ask for the campaign."""
    await state.update_data(channel=_optional(message.text or ""))
    await state.set_state(CreateSheet.waiting_for_campaign)
    await formatting.answer(
        message,
        "Chiến dịch hoặc dự án? Ví dụ " + formatting.code("Ra mắt dịch vụ tháng 8") + ".\n"
        "Gửi " + formatting.code("-") + " để bỏ qua.",
    )


@router.message(CreateSheet.waiting_for_campaign, F.text)
async def handle_creation_campaign(
    message: Message,
    state: FSMContext,
    database: Database,
    settings: Settings,
    drive_client: DriveClient,
) -> None:
    """Record the campaign and ask for the destination folder."""
    await state.update_data(campaign=_optional(message.text or ""))
    await _ask_for_folder(message, state, database, settings, drive_client)


async def _ask_for_folder(
    message: Message,
    state: FSMContext,
    database: Database,
    settings: Settings,
    drive_client: DriveClient,
) -> None:
    """Offer the registered destination folders. Never a free-form path."""
    async with database.session() as session:
        folders = await DriveFolderService(
            session, AuditService(session), drive_client, settings
        ).list_usable()

    if not folders:
        await state.clear()
        await formatting.answer(
            message,
            "⛔ Chưa có thư mục Drive nào được đăng ký và xác thực.\n"
            "OWNER/ADMIN cần chạy /add_drive_folder trước khi tạo Sheet.",
        )
        return

    choices = folders[:MAX_FOLDER_BUTTONS]
    await state.update_data(folder_choices=[str(folder.id) for folder in choices])
    await state.set_state(CreateSheet.waiting_for_folder)
    await formatting.answer(
        message,
        "Chọn thư mục đích:",
        reply_markup=formatting.keyboard(
            [[(folder.name[:40], f"dfd:{index}")] for index, folder in enumerate(choices)]
        ),
    )


@router.callback_query(CreateSheet.waiting_for_folder, F.data.startswith("dfd:"))
async def handle_creation_folder(
    query: CallbackQuery,
    actor: Actor,
    state: FSMContext,
    database: Database,
    settings: Settings,
    drive_client: DriveClient,
    sheets_client: SheetsClient,
) -> None:
    """Resolve the chosen folder and show the full preview."""
    await query.answer()
    data = await state.get_data()
    choices: list[str] = list(data.get("folder_choices", []))
    try:
        index = int((query.data or "dfd:").split(":", 1)[1])
        folder_uuid = uuid.UUID(choices[index])
    except (ValueError, IndexError):
        await state.clear()
        await formatting.answer_callback(
            query, "Lựa chọn không hợp lệ. Gõ lại lệnh tạo Sheet để bắt đầu lại."
        )
        return

    kind = TemplateKind(str(data.get("kind")))
    spec = template_for_kind(kind)

    try:
        async with database.transaction() as session:
            audit = AuditService(session)
            folders = DriveFolderService(session, audit, drive_client, settings)
            folder = await folders.get(folder_uuid)
            folders.assert_usable_by(folder, actor)

            request = _build_request(data, folder.drive_folder_id, kind, actor, str(query.id))
            creation = SpreadsheetCreationService(
                session,
                audit,
                drive_client,
                sheets_client,
                settings,
            )
            preview = await creation.preview(request)
    except MeoBotError as exc:
        await state.clear()
        await formatting.answer_callback(query, "⛔ " + formatting.escape(exc.message))
        return

    await state.update_data(
        folder_drive_id=folder.drive_folder_id,
        folder_name=folder.name,
        confirmation_reference=str(query.id),
    )
    await state.set_state(CreateSheet.waiting_for_confirmation)
    await formatting.answer_callback(
        query,
        _render_preview(preview, spec.expected_tabs, kind),
        reply_markup=formatting.keyboard(
            [[("✅ Tạo Sheet", "dcreate:ok"), ("❌ Huỷ", "dcreate:cancel")]]
        ),
    )


def _build_request(
    data: dict[str, object],
    folder_drive_id: str,
    kind: TemplateKind,
    actor: Actor,
    confirmation_reference: str,
) -> SpreadsheetRequest:
    """Assemble the validated creation request from the collected answers."""
    spec = template_for_kind(kind)
    return SpreadsheetRequest(
        template_code=spec.code,
        template_version=spec.version,
        name=str(data.get("name") or "Sheet"),
        folder_id=folder_drive_id,
        kind=kind,
        team=_as_optional_str(data.get("team")),
        channel=_as_optional_str(data.get("channel")),
        campaign=_as_optional_str(data.get("campaign")),
        period=_as_optional_str(data.get("period")),
        actor_reference=str(actor.telegram_user_id or actor.user_id or ""),
        confirmation_reference=confirmation_reference,
        register_profile=kind is TemplateKind.SCRIPT_MANAGEMENT,
        run_initial_sync=kind is TemplateKind.SCRIPT_MANAGEMENT,
    )


def _as_optional_str(value: object) -> str | None:
    return str(value) if isinstance(value, str) and value.strip() else None


def _render_preview(
    preview: dict[str, object],
    tabs: list[str],
    kind: TemplateKind,
) -> str:
    """Show exactly what will be created, before it is created."""
    method = (
        "sao chép mẫu có sẵn định dạng"
        if preview.get("creation_method") == "template_copy"
        else "tạo Sheet trắng theo chuẩn"
    )
    lines = [
        "📋 " + formatting.bold("Xác nhận tạo Sheet"),
        f"Tên file: {formatting.bold(preview.get('name'))}",
        f"Mẫu: {formatting.escape(preview.get('template_name'))} "
        f"v{formatting.escape(preview.get('template_version'))} ({formatting.escape(method)})",
        f"Thư mục đích: {formatting.escape(preview.get('folder_name'))}"
        + (" (Shared Drive)" if preview.get("shared_drive") else ""),
        f"Tab sẽ có: {formatting.escape(', '.join(tabs))}",
    ]
    for label, key in (
        ("Team", "team"),
        ("Kênh", "channel"),
        ("Chiến dịch", "campaign"),
        ("Kỳ", "period"),
    ):
        value = preview.get(key)
        if value:
            lines.append(f"{label}: {formatting.escape(value)}")

    if kind is TemplateKind.SCRIPT_MANAGEMENT:
        raw_columns = preview.get("columns")
        columns = [str(column) for column in raw_columns] if isinstance(raw_columns, list) else []
        lines.append(f"Tab kịch bản: {formatting.escape(preview.get('worksheet'))}")
        lines.append(
            "Cột chính: "
            + formatting.escape(", ".join(columns[:8]))
            + (" …" if len(columns) > 8 else "")
        )
        lines.append("✅ Sẽ tự đăng ký làm Sheet Profile để đồng bộ kịch bản.")
        lines.append("✅ Sẽ chạy đồng bộ lần đầu ngay sau khi tạo.")
    else:
        lines.append("♻️ Sheet công việc KHÔNG được đăng ký làm nguồn kịch bản.")

    lines.append("")
    lines.append(
        formatting.escape("MeoBot không có công cụ xoá file, nên hãy kiểm tra kỹ trước khi tạo.")
    )
    lines.append("Tạo Sheet này?")
    return "\n".join(lines)


@router.callback_query(CreateSheet.waiting_for_confirmation, F.data == "dcreate:ok")
async def handle_creation_confirmed(
    query: CallbackQuery,
    actor: Actor,
    state: FSMContext,
    request_id: uuid.UUID,
) -> None:
    """Enqueue the creation and acknowledge immediately.

    Drive calls take seconds and a template copy can take longer; doing that
    inline would block the Telegram handler. The idempotency key is derived
    from the confirmation reference stored in the FSM, so a second press of
    this button enqueues a task that finds the same row and creates no second
    file.
    """
    await query.answer()
    data = await state.get_data()
    await state.clear()

    kind_value = str(data.get("kind"))
    chat_id = query.message.chat.id if query.message is not None else None
    payload = {
        "name": str(data.get("name") or "Sheet"),
        "folder_drive_id": str(data.get("folder_drive_id") or ""),
        "team": data.get("team"),
        "channel": data.get("channel"),
        "campaign": data.get("campaign"),
        "period": data.get("period"),
        "actor_telegram_id": actor.telegram_user_id,
        "actor_user_id": str(actor.user_id) if actor.user_id else None,
        "actor_role": actor.role.value,
        "confirmation_reference": str(data.get("confirmation_reference") or query.id),
        "notify_chat_id": chat_id,
        "request_id": str(request_id),
    }

    task = (
        create_work_spreadsheet
        if kind_value == TemplateKind.WORK_MANAGEMENT.value
        else create_script_spreadsheet
    )
    task.apply_async(kwargs={"payload": payload})

    await formatting.answer_callback(
        query,
        "⏳ Đang tạo Sheet trên Google Drive. MeoBot sẽ gửi link ngay khi xong.",
    )


@router.callback_query(CreateSheet.waiting_for_confirmation, F.data == "dcreate:cancel")
async def handle_creation_cancelled(query: CallbackQuery, state: FSMContext) -> None:
    """Abandon the creation flow. Nothing was created."""
    await query.answer()
    await state.clear()
    await formatting.edit_callback(query, "Đã huỷ. Chưa có file nào được tạo.")
