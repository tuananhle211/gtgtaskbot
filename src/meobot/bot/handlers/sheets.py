"""Sheet commands and the ``/add_sheet`` conversation.

The conversation is an aiogram FSM backed by PostgreSQL
(:class:`meobot.bot.storage.PostgresStorage`), so a bot restart in the middle
of adding a sheet does not throw away the Owner's answers.

Handlers here stay transport-only: they parse input, call an application
service, and format the reply.
"""

from __future__ import annotations

import uuid

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from meobot.application.audit_service import AuditService
from meobot.application.sheet_inspection_service import SheetInspection, SheetInspectionService
from meobot.application.sheet_profile_service import SheetProfileService
from meobot.core.config import Settings
from meobot.core.errors import MeoBotError
from meobot.core.logging import get_logger
from meobot.core.time import format_local
from meobot.db.session import Database
from meobot.domain.identity.models import Actor
from meobot.domain.permissions.matrix import Permission, has_permission
from meobot.domain.sheets.mapping import build_field_mapping
from meobot.domain.sheets.models import extract_spreadsheet_id, spreadsheet_url
from meobot.integrations.google.sheets import SheetsClient
from meobot.integrations.llm.base import LLMProvider
from meobot.tasks.sheets import sync_all_active_profiles, sync_profile

logger = get_logger(__name__)

router = Router(name="sheets")

#: Worksheets offered as buttons; more than this and the Owner types the name.
MAX_WORKSHEET_BUTTONS = 12
SAMPLE_PREVIEW_CHARS = 40


class AddSheet(StatesGroup):
    """Steps of ``/add_sheet``."""

    waiting_for_url = State()
    waiting_for_worksheet = State()
    waiting_for_confirmation = State()
    waiting_for_mapping_fix = State()
    waiting_for_name = State()


def _require(actor: Actor, permission: Permission) -> bool:
    return has_permission(actor.role, permission)


@router.message(Command("sheets"))
async def handle_sheets(
    message: Message,
    actor: Actor,
    database: Database,
    settings: Settings,
) -> None:
    """List registered sheet profiles with their sync state."""
    if not _require(actor, Permission.SHEET_PROFILE_READ):
        await message.answer("⛔ Bạn không có quyền xem cấu hình Google Sheet.")
        return

    async with database.session() as session:
        service = SheetProfileService(session, AuditService(session))
        profiles = list(await service.list_profiles(active_only=False))
        counts = await service.script_counts()

    if not profiles:
        await message.answer("Chưa có Google Sheet nào. Dùng /add_sheet để thêm.")
        return

    lines = ["📊 *Google Sheet đã cấu hình:*"]
    for profile in profiles:
        synced = (
            format_local(profile.last_synced_at, settings.timezone, "%d/%m %H:%M")
            if profile.last_synced_at
            else "chưa đồng bộ"
        )
        lines.append(
            f"\n• *{profile.name}*\n"
            f"  tab: `{profile.sheet_name}` · trạng thái: {profile.state.value}\n"
            f"  kịch bản: {counts.get(profile.id, 0)} · đồng bộ: {synced}"
        )
        if profile.last_sync_error:
            lines.append(f"  ⚠️ {profile.last_sync_error[:150]}")
    lines.append("\nĐồng bộ ngay: /sync_sheets")
    await message.answer("\n".join(lines), parse_mode="Markdown")


@router.message(Command("add_sheet"))
async def handle_add_sheet(
    message: Message,
    actor: Actor,
    state: FSMContext,
    settings: Settings,
) -> None:
    """Start the guided sheet-registration conversation."""
    if not _require(actor, Permission.SHEET_PROFILE_WRITE):
        await message.answer("⛔ Bạn không có quyền thêm Google Sheet.")
        return
    if not settings.google_enabled:
        await message.answer(
            "⚠️ Google chưa được cấu hình.\n"
            "Cần đặt GOOGLE_SERVICE_ACCOUNT_FILE và mount file service account "
            "trước khi thêm Sheet."
        )
        return

    await state.clear()
    await state.set_state(AddSheet.waiting_for_url)
    await message.answer(
        "📎 Gửi cho MeoBot link Google Sheet (hoặc spreadsheet ID).\n\n"
        "Nhớ chia sẻ Sheet với email service account (quyền Editor nếu muốn ghi ngược).\n"
        "Gõ /cancel_flow để huỷ."
    )


@router.message(Command("cancel_flow"))
async def handle_cancel_flow(message: Message, state: FSMContext) -> None:
    """Abandon whatever multi-step flow is in progress."""
    current = await state.get_state()
    await state.clear()
    if current is None:
        await message.answer("Không có thao tác nào đang chờ.")
        return
    await message.answer("Đã huỷ thao tác đang thực hiện.")


@router.message(AddSheet.waiting_for_url, F.text)
async def handle_sheet_url(
    message: Message,
    state: FSMContext,
    sheets_client: SheetsClient,
) -> None:
    """Extract the spreadsheet id and list its worksheets."""
    spreadsheet_id = extract_spreadsheet_id(message.text or "")
    if spreadsheet_id is None:
        await message.answer(
            "Không nhận ra link. Hãy gửi URL dạng "
            "https://docs.google.com/spreadsheets/d/<ID>/edit hoặc chính ID đó."
        )
        return

    await message.answer("⏳ Đang đọc danh sách tab...")
    try:
        metadata = await SheetInspectionService(sheets_client).list_worksheets(spreadsheet_id)
    except MeoBotError as exc:
        await state.clear()
        await message.answer(f"⛔ {exc.message}")
        return

    if not metadata.worksheets:
        await state.clear()
        await message.answer("Spreadsheet này không có tab nào đọc được.")
        return

    await state.update_data(
        spreadsheet_id=spreadsheet_id,
        spreadsheet_title=metadata.title,
        worksheets=metadata.worksheet_names,
    )
    await state.set_state(AddSheet.waiting_for_worksheet)

    names = metadata.worksheet_names[:MAX_WORKSHEET_BUTTONS]
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=name[:30], callback_data=f"ws:{index}")]
            for index, name in enumerate(names)
        ]
    )
    await message.answer(
        f"📄 *{metadata.title}*\nChọn tab chứa kịch bản:",
        reply_markup=keyboard,
        parse_mode="Markdown",
    )


@router.callback_query(AddSheet.waiting_for_worksheet, F.data.startswith("ws:"))
async def handle_worksheet_choice(
    query: CallbackQuery,
    state: FSMContext,
    sheets_client: SheetsClient,
    llm_provider: LLMProvider,
) -> None:
    """Inspect the chosen tab and propose a mapping."""
    await query.answer()
    data = await state.get_data()
    worksheets: list[str] = list(data.get("worksheets", []))
    try:
        index = int((query.data or "ws:").split(":", 1)[1])
        sheet_name = worksheets[index]
    except (ValueError, IndexError):
        await _edit(query, "Lựa chọn không hợp lệ. Gõ /add_sheet để bắt đầu lại.")
        await state.clear()
        return

    await _edit(query, f"⏳ Đang đọc cấu trúc tab `{sheet_name}`...")
    try:
        inspection = await SheetInspectionService(sheets_client, llm_provider).inspect(
            spreadsheet_id=str(data["spreadsheet_id"]),
            sheet_name=sheet_name,
            spreadsheet_title=str(data.get("spreadsheet_title", "")),
        )
    except MeoBotError as exc:
        await state.clear()
        await _send(query, f"⛔ {exc.message}")
        return

    await state.update_data(
        sheet_name=sheet_name,
        headers=inspection.headers,
        mapping=inspection.proposal.mapping,
        write_back=inspection.write_back,
    )
    await state.set_state(AddSheet.waiting_for_confirmation)
    await _send(query, _mapping_message(inspection), keyboard=_confirmation_keyboard())


@router.callback_query(AddSheet.waiting_for_confirmation, F.data == "map:ok")
async def handle_mapping_confirmed(query: CallbackQuery, state: FSMContext) -> None:
    """Accept the proposal and ask for a profile name."""
    await query.answer()
    data = await state.get_data()
    await state.set_state(AddSheet.waiting_for_name)
    default = f"{data.get('spreadsheet_title') or 'Sheet'} · {data.get('sheet_name')}"
    await state.update_data(default_name=default)
    await _send(
        query,
        f"Đặt tên cho Sheet profile này (gửi `-` để dùng: {default})",
    )


@router.callback_query(AddSheet.waiting_for_confirmation, F.data == "map:edit")
async def handle_mapping_edit(query: CallbackQuery, state: FSMContext) -> None:
    """Let the Owner correct the proposal by typing pairs."""
    await query.answer()
    await state.set_state(AddSheet.waiting_for_mapping_fix)
    await _send(
        query,
        "✏️ Gửi các dòng sửa theo dạng `trường = tên cột`, ví dụ:\n"
        "```\nscript_body = Kịch bản video\nhook = Mở đầu\n```\n"
        "Trường hợp lệ: script_id, title, hook, script_body, production_notes, "
        "author, deadline, source_status.\n"
        "Gửi `xong` khi đã sửa xong.",
    )


@router.callback_query(AddSheet.waiting_for_confirmation, F.data == "map:cancel")
async def handle_mapping_cancel(query: CallbackQuery, state: FSMContext) -> None:
    """Abandon the flow."""
    await query.answer()
    await state.clear()
    await _send(query, "Đã huỷ thêm Sheet.")


@router.message(AddSheet.waiting_for_mapping_fix, F.text)
async def handle_mapping_fix(message: Message, state: FSMContext) -> None:
    """Apply typed corrections to the proposed mapping."""
    text = (message.text or "").strip()
    data = await state.get_data()
    mapping: dict[str, str] = dict(data.get("mapping", {}))
    headers: list[str] = list(data.get("headers", []))

    if text.lower() in {"xong", "done", "ok"}:
        await state.update_data(mapping=mapping)
        await state.set_state(AddSheet.waiting_for_confirmation)
        await message.answer(
            _mapping_lines(mapping, headers), reply_markup=_confirmation_keyboard()
        )
        return

    applied, rejected = _apply_corrections(text, mapping, headers)
    await state.update_data(mapping=mapping)
    reply = []
    if applied:
        reply.append("✅ Đã cập nhật: " + ", ".join(applied))
    if rejected:
        reply.append("⚠️ Không áp dụng được: " + ", ".join(rejected))
    reply.append("\n" + _mapping_lines(mapping, headers))
    reply.append("\nGửi tiếp dòng sửa, hoặc `xong` để tiếp tục.")
    await message.answer("\n".join(reply))


@router.message(AddSheet.waiting_for_name, F.text)
async def handle_profile_name(
    message: Message,
    actor: Actor,
    state: FSMContext,
    database: Database,
    request_id: uuid.UUID,
) -> None:
    """Persist the profile and offer an immediate sync."""
    data = await state.get_data()
    typed = (message.text or "").strip()
    name = str(data.get("default_name", "Sheet")) if typed == "-" else typed
    mapping = dict(data.get("mapping", {}))
    headers = list(data.get("headers", []))

    try:
        field_mapping = build_field_mapping(mapping)
    except MeoBotError as exc:
        await message.answer(f"⛔ {exc.message}\nGõ /add_sheet để làm lại.")
        await state.clear()
        return

    try:
        async with database.transaction() as session:
            service = SheetProfileService(session, AuditService(session))
            profile = await service.create_profile(
                actor=actor,
                request_id=request_id,
                name=name[:200],
                spreadsheet_id=str(data["spreadsheet_id"]),
                sheet_name=str(data["sheet_name"]),
                field_mapping=field_mapping.model_dump(mode="json"),
                headers=headers,
                write_back_mapping=dict(data.get("write_back", {})),
                spreadsheet_link=spreadsheet_url(str(data["spreadsheet_id"])),
            )
            profile_id = profile.id
            write_back = profile.write_back_mapping
    except MeoBotError as exc:
        await state.clear()
        await message.answer(f"⛔ {exc.message}")
        return

    await state.clear()
    lines = [
        f"✅ Đã lưu Sheet profile *{name}*.",
        f"Mã: `{profile_id}`",
    ]
    lines.append(
        "📝 Ghi ngược vào Sheet: " + (", ".join(sorted(write_back)) if write_back else "không")
    )
    await message.answer("\n".join(lines), parse_mode="Markdown")

    sync_profile.apply_async(
        args=(str(profile_id),),
        kwargs={"notify_chat_id": message.chat.id},
    )
    await message.answer("⏳ Đang đồng bộ lần đầu, MeoBot sẽ báo lại khi xong.")


@router.message(Command("sync_sheets"))
async def handle_sync_sheets(
    message: Message,
    command: CommandObject,
    actor: Actor,
    database: Database,
) -> None:
    """Queue a synchronisation and acknowledge immediately."""
    if not _require(actor, Permission.SHEET_PROFILE_WRITE):
        await message.answer("⛔ Bạn không có quyền đồng bộ Google Sheet.")
        return

    argument = (command.args or "").strip()
    if argument:
        async with database.session() as session:
            service = SheetProfileService(session, AuditService(session))
            profiles = [
                profile
                for profile in await service.list_profiles(active_only=False)
                if str(profile.id).startswith(argument) or profile.name.lower() == argument.lower()
            ]
        if len(profiles) != 1:
            await message.answer(
                "Không xác định được Sheet profile. Dùng /sheets để xem danh sách."
            )
            return
        sync_profile.apply_async(
            args=(str(profiles[0].id),),
            kwargs={"notify_chat_id": message.chat.id},
        )
        await message.answer(f"⏳ Đang đồng bộ {profiles[0].name}...")
        return

    sync_all_active_profiles.apply_async(kwargs={"notify_chat_id": message.chat.id})
    await message.answer("⏳ Đang đồng bộ tất cả Sheet đang hoạt động...")


# --- Helpers ---------------------------------------------------------------
def _confirmation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Đúng rồi", callback_data="map:ok"),
                InlineKeyboardButton(text="✏️ Sửa mapping", callback_data="map:edit"),
            ],
            [InlineKeyboardButton(text="❌ Huỷ", callback_data="map:cancel")],
        ]
    )


def _mapping_message(inspection: SheetInspection) -> str:
    """Render headers, sample rows and the proposal for confirmation."""
    lines = [
        f"📄 Tab `{inspection.sheet_name}` — {len(inspection.headers)} cột.",
        "",
        _mapping_lines(inspection.proposal.mapping, inspection.headers),
    ]
    if inspection.write_back:
        lines.append(
            "\n📝 Cột ghi ngược tự nhận diện: "
            + ", ".join(f"{field} → {header!r}" for field, header in inspection.write_back.items())
        )
    if inspection.sample_rows:
        preview = inspection.sample_rows[0]
        sample = "; ".join(
            f"{key}={value[:SAMPLE_PREVIEW_CHARS]}" for key, value in list(preview.items())[:4]
        )
        lines.append(f"\n🔎 Dòng mẫu: {sample}")
    lines.append("\nMapping này đúng chưa?")
    return "\n".join(lines)


def _mapping_lines(mapping: dict[str, str], headers: list[str]) -> str:
    if not mapping:
        return "❌ Chưa map được cột nào."
    mapped = "\n".join(f"  • {field} ← {header!r}" for field, header in mapping.items())
    unused = [header for header in headers if header not in mapping.values()]
    text = f"Mapping đề xuất:\n{mapped}"
    if unused:
        text += "\nCột chưa dùng: " + ", ".join(repr(header) for header in unused[:10])
    return text


def _apply_corrections(
    text: str,
    mapping: dict[str, str],
    headers: list[str],
) -> tuple[list[str], list[str]]:
    """Parse ``field = header`` lines into ``mapping``, in place."""
    from meobot.domain.sheets.models import CANONICAL_FIELDS, normalize_header

    known = {normalize_header(header): header for header in headers}
    applied: list[str] = []
    rejected: list[str] = []
    for line in text.splitlines():
        if "=" not in line:
            continue
        raw_field, raw_header = line.split("=", 1)
        field = raw_field.strip().lower()
        header_text = raw_header.strip()
        if field not in CANONICAL_FIELDS:
            rejected.append(f"{field} (trường không hợp lệ)")
            continue
        if not header_text or header_text == "-":
            mapping.pop(field, None)
            applied.append(f"{field} (bỏ map)")
            continue
        actual = known.get(normalize_header(header_text))
        if actual is None:
            rejected.append(f"{field} (không có cột {header_text!r})")
            continue
        mapping[field] = actual
        applied.append(field)
    return applied, rejected


async def _edit(query: CallbackQuery, text: str) -> None:
    """Replace the message a button belongs to, ignoring 'not modified'."""
    if isinstance(query.message, Message):
        try:
            await query.message.edit_text(text)
            return
        except Exception:
            logger.debug("callback_edit_failed")
    await _send(query, text)


async def _send(
    query: CallbackQuery,
    text: str,
    keyboard: InlineKeyboardMarkup | None = None,
) -> None:
    """Answer in the chat a callback came from."""
    if isinstance(query.message, Message):
        await query.message.answer(text, reply_markup=keyboard)
