"""Script commands and the review inline buttons.

The buttons are the Owner's real interface: a review lands in the chat, and
approving or rejecting is one tap. Every tap is re-validated server-side -
signature, permission, workflow state, and the exact version the button was
drawn for - because a callback payload is client-supplied data.
"""

from __future__ import annotations

import uuid

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from meobot.application.audit_service import AuditService
from meobot.application.script_presenter import (
    format_full_script,
    format_pending_list,
    format_script_detail,
    short_id,
)
from meobot.application.script_service import ScriptService
from meobot.core.config import Settings
from meobot.core.errors import MeoBotError
from meobot.core.logging import get_logger
from meobot.db.session import Database
from meobot.domain.conversations.callbacks import CallbackAction, CallbackPayload, parse_callback
from meobot.domain.identity.models import Actor
from meobot.domain.permissions.matrix import Permission, has_permission
from meobot.tasks.scripts import push_decision_to_sheet, review_script

logger = get_logger(__name__)

router = Router(name="scripts")

PAGE_SIZE = 10


class RevisionFlow(StatesGroup):
    """Waiting for the Owner to type a revision comment."""

    waiting_for_comment = State()


@router.message(Command("pending_scripts"))
async def handle_pending(
    message: Message,
    command: CommandObject,
    actor: Actor,
    database: Database,
) -> None:
    """List scripts waiting for review or approval."""
    if not has_permission(actor.role, Permission.SCRIPT_READ):
        await message.answer("⛔ Bạn không có quyền xem kịch bản.")
        return

    offset = _parse_offset(command.args)
    async with database.session() as session:
        service = ScriptService(session, AuditService(session))
        scripts = list(await service.list_pending(limit=PAGE_SIZE, offset=offset))
        total = await service.count_pending()

    text = format_pending_list(scripts, total=total, offset=offset)
    if offset + len(scripts) < total:
        text += f"\nTrang sau: /pending_scripts {offset + PAGE_SIZE}"
    await message.answer(text, parse_mode="Markdown")


@router.message(Command("script"))
async def handle_script(
    message: Message,
    command: CommandObject,
    actor: Actor,
    database: Database,
    settings: Settings,
) -> None:
    """Show one script in full detail."""
    if not has_permission(actor.role, Permission.SCRIPT_READ):
        await message.answer("⛔ Bạn không có quyền xem kịch bản.")
        return

    reference = (command.args or "").strip()
    if not reference:
        await message.answer("Cú pháp: /script <mã kịch bản>")
        return

    try:
        async with database.session() as session:
            service = ScriptService(session, AuditService(session))
            script = await service.resolve(reference)
            detail = await service.detail(script.id)
    except MeoBotError as exc:
        await message.answer(f"⛔ {exc.message}")
        return

    await message.answer(
        format_script_detail(detail, timezone=settings.timezone),
        parse_mode="Markdown",
    )


@router.message(Command("review_script"))
async def handle_review_script(
    message: Message,
    command: CommandObject,
    actor: Actor,
    database: Database,
) -> None:
    """Queue an AI review and acknowledge immediately."""
    if not has_permission(actor.role, Permission.SCRIPT_REVIEW):
        await message.answer("⛔ Bạn không có quyền yêu cầu AI review.")
        return

    reference = (command.args or "").strip()
    if not reference:
        await message.answer("Cú pháp: /review_script <mã kịch bản>")
        return

    try:
        async with database.session() as session:
            script = await ScriptService(session, AuditService(session)).resolve(reference)
            script_id = script.id
    except MeoBotError as exc:
        await message.answer(f"⛔ {exc.message}")
        return

    review_script.apply_async(
        args=(str(script_id),),
        kwargs={"notify_chat_id": message.chat.id},
    )
    await message.answer(
        f"⏳ Đã xếp hàng review cho `{short_id(script_id)}`. MeoBot sẽ gửi kết quả ngay khi xong.",
        parse_mode="Markdown",
    )


# --- Inline buttons --------------------------------------------------------
@router.callback_query(F.data.regexp(r"^(ap|rr|ra|vs|sk)\|"))
async def handle_script_callback(
    query: CallbackQuery,
    actor: Actor,
    database: Database,
    settings: Settings,
    request_id: uuid.UUID,
    state: FSMContext,
) -> None:
    """Dispatch a signed review-message button."""
    payload = parse_callback(query.data or "", secret=settings.callback_secret)
    if payload is None or payload.entity_id is None:
        await query.answer("Nút không hợp lệ hoặc đã hết hiệu lực.", show_alert=True)
        return

    try:
        if payload.action is CallbackAction.VIEW_SCRIPT:
            await _view(query, payload, database)
        elif payload.action is CallbackAction.SKIP:
            await query.answer("Đã bỏ qua.")
        elif payload.action is CallbackAction.REVIEW_AGAIN:
            await _review_again(query, payload, actor)
        elif payload.action is CallbackAction.APPROVE_PRODUCTION:
            await _approve(query, payload, actor, database, request_id, settings)
        elif payload.action is CallbackAction.REQUEST_REVISION:
            await _ask_for_comment(query, payload, actor, state)
        else:  # pragma: no cover - mapping buttons are handled elsewhere
            await query.answer()
    except MeoBotError as exc:
        logger.info("script_callback_refused", extra={"error_code": exc.code})
        await query.answer(exc.message[:200], show_alert=True)


async def _view(query: CallbackQuery, payload: CallbackPayload, database: Database) -> None:
    """Show the full script body."""
    assert payload.entity_id is not None
    async with database.session() as session:
        detail = await ScriptService(session, AuditService(session)).detail(payload.entity_id)
    await query.answer()
    if isinstance(query.message, Message):
        await query.message.answer(format_full_script(detail), parse_mode="Markdown")


async def _review_again(query: CallbackQuery, payload: CallbackPayload, actor: Actor) -> None:
    """Queue a fresh review of the current version."""
    assert payload.entity_id is not None
    if not has_permission(actor.role, Permission.SCRIPT_REVIEW):
        await query.answer("Bạn không có quyền yêu cầu review.", show_alert=True)
        return
    chat_id = query.message.chat.id if isinstance(query.message, Message) else None
    review_script.apply_async(
        args=(str(payload.entity_id),),
        kwargs={"notify_chat_id": chat_id},
    )
    await query.answer("Đã xếp hàng review lại.")


async def _approve(
    query: CallbackQuery,
    payload: CallbackPayload,
    actor: Actor,
    database: Database,
    request_id: uuid.UUID,
    settings: Settings,
) -> None:
    """Approve the exact version the button was drawn for."""
    assert payload.entity_id is not None
    async with database.transaction() as session:
        service = ScriptService(session, AuditService(session))
        version = await service.version_by_number(payload.entity_id, int(payload.argument or 0))
        if version is None:
            await query.answer("Không tìm thấy phiên bản này.", show_alert=True)
            return
        approval = await service.approve_for_production(
            actor=actor,
            request_id=request_id,
            script_id=payload.entity_id,
            expected_version_id=version.id,
        )
        version_number = version.version_number
        approved_id = approval.script_id

    await query.answer("Đã duyệt cho sản xuất.")
    if isinstance(query.message, Message):
        await query.message.answer(
            f"✅ *Đã duyệt SẢN XUẤT* `{short_id(approved_id)}` (v{version_number})\n"
            f"Người duyệt: {actor.display_name}\n\n"
            "_Duyệt sản xuất không cấp quyền đăng bài. "
            "Việc đăng cần một lần duyệt video riêng._",
            parse_mode="Markdown",
        )

    push_decision_to_sheet.apply_async(
        args=(str(approved_id),),
        kwargs={"approved_by": actor.display_name},
    )


async def _ask_for_comment(
    query: CallbackQuery,
    payload: CallbackPayload,
    actor: Actor,
    state: FSMContext,
) -> None:
    """Start the short conversation that collects a revision comment."""
    assert payload.entity_id is not None
    if not has_permission(actor.role, Permission.SCRIPT_APPROVE):
        await query.answer("Bạn không có quyền yêu cầu sửa.", show_alert=True)
        return
    await state.set_state(RevisionFlow.waiting_for_comment)
    await state.update_data(
        script_id=str(payload.entity_id),
        version_number=int(payload.argument or 0),
    )
    await query.answer()
    if isinstance(query.message, Message):
        await query.message.answer(
            "✏️ Gửi ghi chú cho tác giả (hoặc gửi `-` nếu không cần ghi chú)."
        )


@router.message(RevisionFlow.waiting_for_comment, F.text)
async def handle_revision_comment(
    message: Message,
    actor: Actor,
    state: FSMContext,
    database: Database,
    request_id: uuid.UUID,
) -> None:
    """Record the revision request with the typed comment."""
    data = await state.get_data()
    await state.clear()
    raw = (message.text or "").strip()
    comment = None if raw == "-" else raw[:2000]

    try:
        script_id = uuid.UUID(str(data.get("script_id")))
    except (ValueError, TypeError):
        await message.answer("Không xác định được kịch bản. Hãy thử lại từ /pending_scripts.")
        return

    try:
        async with database.transaction() as session:
            service = ScriptService(session, AuditService(session))
            version = await service.version_by_number(script_id, int(data.get("version_number", 0)))
            await service.request_revision(
                actor=actor,
                request_id=request_id,
                script_id=script_id,
                comment=comment,
                expected_version_id=version.id if version is not None else None,
            )
    except MeoBotError as exc:
        await message.answer(f"⛔ {exc.message}")
        return

    await message.answer(f"✏️ Đã yêu cầu sửa `{short_id(script_id)}`.", parse_mode="Markdown")
    push_decision_to_sheet.apply_async(
        args=(str(script_id),),
        kwargs={"revision_comment": comment},
    )


def _parse_offset(argument: str | None) -> int:
    """Parse the page offset from ``/pending_scripts <offset>``."""
    try:
        return max(0, int((argument or "0").strip()))
    except ValueError:
        return 0
