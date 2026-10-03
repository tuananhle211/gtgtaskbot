"""Slash-command handlers: system, help and confirmation.

``/help`` is built from :mod:`meobot.bot.commands` and sent through
:mod:`meobot.bot.formatting`. Both of those exist because of the bug this file
used to contain: the help text was a hand-maintained Markdown constant, and its
seven underscores (``/script_types``, ``/add_sheet``, ...) were an odd number of
italic delimiters. Telegram rejected the message with ``400 Can't find end of
Italic entity``, aiogram raised, and the user saw nothing.

Two rules follow from that and are enforced for every handler here:

* commands are rendered from the registry, never from a copy of it;
* every send goes through :func:`meobot.bot.formatting.answer`, which escapes
  dynamic text, splits over-long messages, and falls back to plain text rather
  than failing silently.
"""

from __future__ import annotations

import uuid

from aiogram import Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from meobot.application.actor_profile_service import ActorProfileService
from meobot.application.assistant_context_service import AssistantContextService
from meobot.application.audit_service import AuditService
from meobot.application.capability_service import CapabilityService
from meobot.application.conversation_service import ConversationService
from meobot.application.health_service import HealthService
from meobot.application.identity_service import IdentityService
from meobot.application.script_type_service import ScriptTypeService
from meobot.bot import formatting
from meobot.bot.commands import render_help, spec_for
from meobot.bot.handlers.reminders import resume_parked_reminder
from meobot.bot.texts import welcome_for
from meobot.core.config import Settings
from meobot.core.errors import MeoBotError
from meobot.core.logging import get_logger
from meobot.db.session import Database
from meobot.domain.identity.labels import actor_label
from meobot.domain.identity.models import Actor, Role
from meobot.tools.base import ToolRegistry

logger = get_logger(__name__)

router = Router(name="commands")


@router.message(CommandStart())
async def handle_start(
    message: Message,
    actor: Actor,
    database: Database,
    settings: Settings,
    state: FSMContext,
) -> None:
    """Confirm who this is, make the configured owner real, and resume.

    Three jobs, and the second one is the hotfix. Until 0.6.0a2.1 ``/start``
    only *said* "vai trò Chủ sở hữu": the configured owner was resolved from
    configuration and no ``users`` row was ever written, so reminders and HR -
    which need something to own their records - refused with a confusing
    message about a missing "hồ sơ".

    Registered on the commands router with **no state filter**, which in
    aiogram 3 means it matches in any FSM state. That is what stops a
    half-finished reminder from swallowing the very command that fixes it.
    """
    owner_actor = actor
    if actor.is_bootstrap_owner and message.from_user is not None:
        async with database.transaction() as session:
            user = await IdentityService(session, settings).ensure_bootstrap_owner(
                telegram_user_id=message.from_user.id,
                telegram_username=message.from_user.username,
                full_name=message.from_user.full_name,
                # Only from a private chat: a group chat id is not somewhere
                # MeoBot may send this person's private notifications.
                private_chat_id=message.chat.id if message.chat.type == "private" else None,
            )
            if user is not None:
                owner_actor = actor.model_copy(
                    update={"user_id": user.id, "is_bootstrap_owner": False}
                )

    greeting = welcome_for(owner_actor.role, owner_actor.full_name, guest=actor.is_guest)
    await formatting.answer(
        message,
        formatting.escape(greeting)
        + "\n\n"
        + formatting.escape("Gõ /help để xem mình làm được gì."),
    )

    # A request parked waiting for exactly this is picked up now, so the person
    # does not have to type it again.
    await resume_parked_reminder(message, owner_actor, database, settings, state)


@router.message(Command("help"))
async def handle_help(message: Message, actor: Actor | None = None) -> None:
    """List the commands this actor may use.

    Registered on the commands router, which is included first, and with no
    state filter - so it answers in a normal chat *and* in the middle of
    ``/add_sheet`` or any other guided flow.

    ``actor`` is optional because ``/help`` is a public command: an
    unregistered account reaches this handler with no actor bound (see
    :class:`~meobot.bot.middlewares.ActorMiddleware`) and gets the short help
    that tells them to redeem an invite code.
    """
    if actor is None:
        await formatting.answer(
            message,
            render_help(Role.EMPLOYEE, unregistered=True)
            + "\n\n"
            + formatting.escape(
                "Tài khoản này chưa được đăng ký với MeoBot. "
                "Xin quản trị viên một mã mời rồi gõ /join <mã>."
            ),
        )
        return
    await formatting.answer(message, render_help(actor.role))


@router.message(Command("capabilities"))
async def handle_capabilities(
    message: Message,
    actor: Actor,
    tool_registry: ToolRegistry,
    settings: Settings,
) -> None:
    """Report what MeoBot can do for this actor, from live configuration."""
    report = CapabilityService(tool_registry, settings).report_for(actor)
    sections: list[str] = [
        formatting.bold("MeoBot làm được gì"),
        formatting.escape(f"Vai trò của bạn: {actor_label(actor)}"),
    ]

    def block(title: str, items: list[str], marker: str) -> None:
        if not items:
            return
        sections.append("")
        sections.append(formatting.bold(title))
        sections.extend(f"{marker} {formatting.escape(item)}" for item in items)

    block("Dùng được ngay", report.available_now, "✅")
    block("Cần cấu hình thêm", report.needs_configuration, "⚙️")
    block("Vai trò của bạn chưa được phép", report.not_permitted, "🔒")
    block("Chưa xây dựng", report.not_implemented, "🚧")

    await formatting.answer(message, "\n".join(sections))


@router.message(Command("whoami"))
async def handle_whoami(
    message: Message,
    actor: Actor,
    database: Database,
    settings: Settings,
) -> None:
    """Show the profile MeoBot is using for this person.

    Role comes from the identity service, not from the profile, and is
    displayed as such: a profile is context, never authority.
    """
    async with database.session() as session:
        profile = await ActorProfileService(session, settings).profile_for(actor)

    lines = [
        "👤 " + formatting.bold("MeoBot đang nói chuyện với"),
        formatting.escape(f"Tên: {profile.display_name or actor.full_name}"),
        formatting.escape(f"Vai trò (từ hệ thống định danh): {actor_label(actor)}"),
        formatting.escape(f"Cách xưng hô: {profile.address}"),
    ]
    for label, value in (
        ("Chức danh", profile.job_title),
        ("Tổ chức", profile.organization),
        ("Phòng ban", profile.department),
        ("Nhóm", profile.team),
    ):
        if value:
            lines.append(formatting.escape(f"{label}: {value}"))
    if profile.responsibilities:
        lines.append("")
        lines.append(formatting.bold("Phụ trách"))
        lines.extend(f"• {formatting.escape(item)}" for item in profile.responsibilities)
    if profile.content_domains:
        lines.append("")
        lines.append(formatting.bold("Mảng nội dung"))
        lines.extend(f"• {formatting.escape(item)}" for item in profile.content_domains)
    lines.append("")
    lines.append(
        formatting.escape("Đổi cách xưng hô: /set_preferred_address <cách bạn muốn được gọi>")
    )
    await formatting.answer(message, "\n".join(lines))


@router.message(Command("assistant_profile"))
async def handle_assistant_profile(
    message: Message,
    actor: Actor,
    database: Database,
    settings: Settings,
) -> None:
    """Show MeoBot's own identity, mission and workspace."""
    async with database.session() as session:
        profile = await AssistantContextService(settings, session).load()

    lines = [
        "🐱 " + formatting.bold(profile.assistant_name),
        formatting.escape(profile.identity),
        "",
        formatting.bold("Nhiệm vụ"),
        formatting.escape(profile.mission),
    ]
    workspace = profile.workspace_line()
    if workspace:
        lines.extend(["", formatting.bold("Nơi làm việc"), formatting.escape(workspace)])
    if profile.operating_domains:
        lines.append("")
        lines.append(formatting.bold("Lĩnh vực"))
        lines.extend(f"• {formatting.escape(item)}" for item in profile.operating_domains)
    if profile.tone:
        lines.append("")
        lines.append(formatting.bold("Giọng điệu"))
        lines.append(formatting.escape(", ".join(profile.tone)))
    await formatting.answer(message, "\n".join(lines))


@router.message(Command("set_preferred_address"))
async def handle_set_preferred_address(
    message: Message,
    command: CommandObject,
    actor: Actor,
    database: Database,
    settings: Settings,
) -> None:
    """Change how MeoBot addresses *this* user, and nobody else.

    Scoped to the caller by construction: the service writes the row keyed by
    the caller's Telegram id. There is no argument that could point it at
    somebody else's profile, and none that touches the assistant's own
    identity - that is deliberately a separate, OWNER-level concern.
    """
    value = (command.args or "").strip()
    if not value:
        await formatting.answer(
            message, formatting.escape(spec_for("set_preferred_address").usage_text())
        )
        return

    try:
        async with database.transaction() as session:
            stored = await ActorProfileService(session, settings).set_preferred_address(
                actor, value
            )
    except ValueError as exc:
        await formatting.answer(message, "⛔ " + formatting.escape(str(exc)))
        return

    await formatting.answer(
        message,
        formatting.escape(f"Từ giờ mình sẽ gọi bạn là “{stored}”."),
    )


@router.message(Command("health"))
async def handle_health(message: Message, actor: Actor, health_service: HealthService) -> None:
    """Report the health of the bot process and its dependencies."""
    report = await health_service.check()
    lines = [
        "🩺 " + formatting.bold("Tình trạng hệ thống"),
        "✅ bot (đang xử lý lệnh này)",
        *(formatting.escape(line) for line in report.as_lines()),
    ]
    await formatting.answer(message, "\n".join(lines))


@router.message(Command("script_types"))
async def handle_script_types(message: Message, actor: Actor, database: Database) -> None:
    """List active script types straight from the registry."""
    async with database.session() as session:
        service = ScriptTypeService(session, AuditService(session))
        script_types = await service.list_script_types(active_only=True)

    if not script_types:
        await formatting.answer(message, "Chưa có thể loại kịch bản nào đang hoạt động.")
        return

    lines = [
        f"• {formatting.escape(item.code)} — {formatting.escape(item.name)} "
        f"(v{item.current_version})"
        for item in script_types
    ]
    await formatting.answer(
        message,
        "📚 " + formatting.bold("Thể loại kịch bản đang hoạt động") + "\n" + "\n".join(lines),
    )


@router.message(Command("confirm"))
async def handle_confirm(
    message: Message,
    command: CommandObject,
    actor: Actor,
    request_id: uuid.UUID,
    conversation_service: ConversationService,
) -> None:
    """Redeem a confirmation token and execute the stored plan."""
    token = (command.args or "").strip()
    if not token:
        await formatting.answer(message, formatting.escape(spec_for("confirm").usage_text()))
        return

    try:
        reply = await conversation_service.confirm(actor=actor, token=token, request_id=request_id)
    except MeoBotError as exc:
        await formatting.answer(message, "⛔ " + formatting.escape(exc.message))
        return
    await formatting.answer(message, formatting.render_assistant_text(reply.text))


@router.message(Command("cancel"))
async def handle_cancel(
    message: Message,
    command: CommandObject,
    actor: Actor,
    request_id: uuid.UUID,
    conversation_service: ConversationService,
) -> None:
    """Reject a pending confirmation."""
    token = (command.args or "").strip()
    if not token:
        await formatting.answer(message, formatting.escape(spec_for("cancel").usage_text()))
        return

    try:
        reply = await conversation_service.cancel_confirmation(
            actor=actor, token=token, request_id=request_id
        )
    except MeoBotError as exc:
        await formatting.answer(message, "⛔ " + formatting.escape(exc.message))
        return
    await formatting.answer(message, formatting.render_assistant_text(reply.text))
