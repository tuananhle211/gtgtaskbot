"""Chat-thread commands: ``/new_chat``, ``/clear_chat``, ``/chat_status``.

These manage conversation *memory*, not conversation itself - the talking
happens in :mod:`meobot.bot.handlers.conversation`.

``/clear_chat`` archives rather than deletes. "Start fresh" and "destroy the
record" are different requests, and only the first one was asked for.
"""

from __future__ import annotations

from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from meobot.application.chat_diagnostics_service import ChatDiagnosticsService
from meobot.application.chat_memory_service import ChatMemoryService
from meobot.bot import formatting
from meobot.core.config import Settings
from meobot.core.logging import get_logger
from meobot.core.time import utcnow
from meobot.db.session import Database
from meobot.domain.identity.models import Actor, Role
from meobot.domain.permissions.matrix import Permission, has_permission
from meobot.integrations.llm.base import LLMProvider
from meobot.integrations.llm.diagnostics import ErrorCategory

logger = get_logger(__name__)

router = Router(name="chat")

CLEAR_CONFIRM = "chat:clear:yes"
CLEAR_CANCEL = "chat:clear:no"


def _target(message: Message) -> tuple[int, int, int] | None:
    """``(bot_id, chat_id, telegram_user_id)`` for this message."""
    if message.from_user is None or message.bot is None:
        return None
    return message.bot.id, message.chat.id, message.from_user.id


def _denied(actor: Actor) -> bool:
    return not has_permission(actor.role, Permission.CONVERSATION_USE)


@router.message(Command("new_chat"))
async def handle_new_chat(
    message: Message,
    actor: Actor,
    database: Database,
    settings: Settings,
) -> None:
    """Archive the current thread and start a fresh one."""
    if _denied(actor):
        await formatting.answer(message, "⛔ Bạn chưa được phép dùng chế độ trò chuyện.")
        return
    target = _target(message)
    if target is None:  # pragma: no cover - Telegram always supplies these
        return

    bot_id, chat_id, user_id = target
    async with database.transaction() as session:
        memory = ChatMemoryService(session, settings)
        await memory.start_thread(bot_id=bot_id, chat_id=chat_id, telegram_user_id=user_id)

    await formatting.answer(
        message,
        "🆕 Đã mở cuộc trò chuyện mới. Nội dung trước đó được lưu lại nhưng "
        "mình sẽ không nhắc tới nữa.",
    )


@router.message(Command("clear_chat"))
async def handle_clear_chat(message: Message, actor: Actor) -> None:
    """Ask before archiving - this is the user's context, not MeoBot's."""
    if _denied(actor):
        await formatting.answer(message, "⛔ Bạn chưa được phép dùng chế độ trò chuyện.")
        return
    await formatting.answer(
        message,
        "Bạn muốn lưu trữ cuộc trò chuyện hiện tại và bắt đầu lại từ đầu?\n"
        "Nội dung cũ không bị xoá, chỉ là mình sẽ không dùng làm bối cảnh nữa.",
        reply_markup=formatting.keyboard(
            [[("✅ Lưu trữ", CLEAR_CONFIRM), ("❌ Giữ nguyên", CLEAR_CANCEL)]]
        ),
    )


@router.callback_query(F.data == CLEAR_CONFIRM)
async def handle_clear_confirmed(
    query: CallbackQuery,
    actor: Actor,
    database: Database,
    settings: Settings,
) -> None:
    """Archive the active thread."""
    await query.answer()
    if _denied(actor) or query.from_user is None or query.message is None:
        return
    bot_id = query.bot.id if query.bot is not None else 0
    async with database.transaction() as session:
        memory = ChatMemoryService(session, settings)
        archived = await memory.archive_active(
            bot_id=bot_id,
            chat_id=query.message.chat.id,
            telegram_user_id=query.from_user.id,
        )
    text = (
        "🗂 Đã lưu trữ cuộc trò chuyện. Lần nhắn tới sẽ bắt đầu một mạch mới."
        if archived
        else "Không có cuộc trò chuyện nào đang mở."
    )
    await formatting.edit_callback(query, text)


@router.callback_query(F.data == CLEAR_CANCEL)
async def handle_clear_cancelled(query: CallbackQuery) -> None:
    """Leave the thread alone."""
    await query.answer()
    await formatting.edit_callback(query, "Giữ nguyên cuộc trò chuyện hiện tại.")


@router.message(Command("chat_status"))
async def handle_chat_status(
    message: Message,
    actor: Actor,
    database: Database,
    settings: Settings,
    llm_provider: LLMProvider,
) -> None:
    """Show whether chat works, which model answers, and how much it remembers.

    Everything here is safe to screenshot. The API key is reported only as
    configured/not configured; the base URL only as a hostname, never with its
    path or query string, because a gateway key in a query string is a real
    pattern; error *categories* are shown, never provider response bodies.
    """
    if _denied(actor):
        await formatting.answer(message, "⛔ Bạn chưa được phép dùng chế độ trò chuyện.")
        return
    target = _target(message)
    if target is None:  # pragma: no cover
        return

    bot_id, chat_id, user_id = target
    async with database.session() as session:
        memory = ChatMemoryService(session, settings)
        thread = await memory.active_thread(
            bot_id=bot_id, chat_id=chat_id, telegram_user_id=user_id
        )
        if thread is None:
            recent = 0
            total = 0
            age_text = "chưa có"
            summarised = False
            thread_label = "chưa có"
        else:
            recent = len(await memory.recent_messages(thread.id))
            total = await memory.message_count(thread.id)
            age_text = _humanize(await memory.thread_age_seconds(thread))
            summarised = await memory.summary_for(thread.id) is not None
            thread_label = str(thread.id)[:8]

    diagnostics = llm_provider.diagnostics
    host = settings.llm_base_url_host or "mặc định của provider"

    lines = [
        "💬 " + formatting.bold("Trạng thái trò chuyện"),
        f"CHAT_ENABLED: {'bật' if settings.chat_enabled else 'tắt'}",
        f"Loại provider: {formatting.escape(llm_provider.name)}"
        + (" (ngoại tuyến)" if settings.llm_is_fake else ""),
        f"Mô hình: {formatting.escape(llm_provider.model)}",
        f"API key: {'đã cấu hình' if settings.llm_key_configured else 'chưa cấu hình'}",
        f"Base URL (host): {formatting.escape(host)}",
        "",
        formatting.bold("Mạch trò chuyện"),
        f"Mạch đang mở: {formatting.escape(thread_label)}",
        f"Số tin nhắn dùng làm bối cảnh: {recent}/{settings.chat_history_max_messages}",
        f"Tổng số lượt trong mạch hiện tại: {total}",
        f"Tuổi cuộc trò chuyện: {formatting.escape(age_text)}",
        f"Đã tóm tắt bối cảnh cũ: {'có' if summarised else 'chưa'}",
        "",
        formatting.bold("Provider"),
        f"Lần gọi thành công gần nhất: {formatting.escape(_when(diagnostics.last_success_at))}",
        "Độ trễ lần thành công gần nhất: "
        + formatting.escape(_latency(diagnostics.last_latency_ms)),
        f"Nhóm lỗi gần nhất: {formatting.escape(diagnostics.last_error_category or 'chưa có')}",
        f"Thời điểm lỗi gần nhất: {formatting.escape(_when(diagnostics.last_error_at))}",
    ]
    if diagnostics.downgrades:
        lines.append(
            "Đã tự điều chỉnh tham số: " + formatting.escape(", ".join(diagnostics.downgrades))
        )
    if diagnostics.last_error_category:
        lines.append(
            formatting.escape("Gợi ý: " + ErrorCategory.hint(diagnostics.last_error_category))
        )
    if settings.llm_is_fake:
        lines.extend(
            [
                "",
                formatting.escape(
                    "Đang chạy ngoại tuyến nên phần trò chuyện chỉ trả lời theo mẫu. "
                    "Đặt LLM_PROVIDER=openai để trò chuyện đầy đủ."
                ),
            ]
        )
    await formatting.answer(message, "\n".join(lines))


@router.message(Command("chat_test"))
async def handle_chat_test(
    message: Message,
    actor: Actor,
    settings: Settings,
    llm_provider: LLMProvider,
) -> None:
    """Probe the provider twice and report the two results separately.

    OWNER-only: it spends real tokens against the configured model, and the
    person who pays for them is the person who may run it.

    The two probes are reported separately because they fail independently. A
    gateway without structured-output support breaks routing while ordinary
    conversation keeps working perfectly - and a single verdict would hide
    exactly that.
    """
    if actor.role is not Role.OWNER:
        await formatting.answer(message, "⛔ Chỉ OWNER được chạy kiểm tra này.")
        return

    await formatting.send_typing(message, enabled=settings.chat_typing_indicator)
    report = await ChatDiagnosticsService(llm_provider, settings).run()

    lines = [
        "🧪 " + formatting.bold("Kiểm tra trợ lý AI"),
        formatting.escape(report.plain_text.render()),
        formatting.escape(report.structured_routing.render()),
        "",
        f"Provider: {formatting.escape(report.provider)}",
        f"Mô hình: {formatting.escape(report.model)}",
        f"JSON schema: {'có' if report.capabilities.supports_json_schema else 'không'}",
        f"JSON object: {'có' if report.capabilities.supports_json_mode else 'không'}",
        f"Tham số độ dài: {formatting.escape(report.capabilities.max_tokens_parameter)}",
        f"Temperature tuỳ chỉnh: {'có' if report.capabilities.supports_temperature else 'không'}",
    ]
    if report.capabilities.downgrades:
        lines.append(
            "Đã tự điều chỉnh: " + formatting.escape(", ".join(report.capabilities.downgrades))
        )
    lines.extend(["", formatting.escape("Khuyến nghị: " + report.recommendation())])
    await formatting.answer(message, "\n".join(lines))


def _when(moment: datetime | None) -> str:
    """A readable "how long ago", or a clear "never"."""
    if moment is None:
        return "chưa có"
    seconds = max(0, int((utcnow() - moment).total_seconds()))
    return f"{_humanize(seconds)} trước"


def _latency(milliseconds: int | None) -> str:
    if milliseconds is None:
        return "chưa có"
    return f"{milliseconds / 1000:.1f}s"


def _humanize(seconds: int) -> str:
    """Readable age for ``/chat_status``."""
    if seconds < 60:
        return f"{seconds} giây"
    if seconds < 3600:
        return f"{seconds // 60} phút"
    if seconds < 86400:
        return f"{seconds // 3600} giờ"
    return f"{seconds // 86400} ngày"
