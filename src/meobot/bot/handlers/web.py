"""``/web`` - the only way into the PR web admin.

Step 1E. One command, one message, and a deliberate set of refusals.

Why this is the only entry point
--------------------------------

The login link is a credential. Telegram is what makes sending it safe: the bot
can only DM somebody who started a conversation with it, and this deployment
already knows which ``users`` row that account belongs to. So the delivery *is*
the proof of identity, and there is no HTTP endpoint that mints links - one would
hand a credential to whoever asked.

The three refusals
------------------

* **not in a group.** A link posted in a group is a link everybody in the group
  can use, and Telegram group history is searchable. The command answers in a
  group only to say "message me privately";
* **not without a ``users`` row.** The bootstrap owner from
  ``MEOBOT_OWNER_TELEGRAM_ID`` is configuration, not a person the session table
  can point at. ``/start`` writes their row; until then there is nothing to
  authenticate;
* **not when unconfigured.** No ``WEB_BASE_URL`` means no known host to send
  somebody to, and a link to the wrong host is a token handed to a stranger's
  server.
"""

from __future__ import annotations

import uuid

from aiogram import Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import Message

from meobot.application.audit_service import AuditService
from meobot.application.web_auth_service import WebAuthService
from meobot.core.config import Settings
from meobot.core.errors import MeoBotError
from meobot.core.logging import get_logger
from meobot.db.session import Database
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.identity.models import Actor

logger = get_logger(__name__)

router = Router(name="web")


@router.message(Command("web"))
async def handle_web(
    message: Message,
    actor: Actor,
    database: Database,
    settings: Settings,
) -> None:
    """Send this person a private, single-use link to the PR panel."""
    if message.chat.type != ChatType.PRIVATE:
        # No link, not even a hint of one. The reply is public by definition.
        await message.answer(
            "🔒 Mình chỉ gửi liên kết đăng nhập trong tin nhắn riêng. "
            "Bạn nhắn riêng cho mình `/web` nhé."
        )
        return

    if actor.user_id is None:
        await message.answer(
            "⛔ Tài khoản của bạn chưa được đăng ký trong hệ thống. Bạn gửi /start trước nhé."
        )
        return

    async with database.transaction() as session:
        try:
            issued = await WebAuthService(session, settings).issue_login_link(user_id=actor.user_id)
        except MeoBotError as exc:
            # Includes the unconfigured case. The person reads a sentence; the
            # reason is in the logs, where whoever runs the deployment will look.
            logger.info("web_login_link_refused", extra={"error_code": exc.code})
            await message.answer(f"⛔ {exc.message}")
            return

        await AuditService(session).record_action(
            request_id=uuid.uuid4(),
            actor=actor,
            action=AuditAction.WEB_LOGIN_LINK_ISSUED,
            result=AuditResult.SUCCESS,
            entity_type="web_session",
            # The URL is **not** recorded. An audit row is read by more people
            # than a session is, and this one would contain a working token.
            after_data={"expires_at": issued.expires_at.isoformat()},
        )

    minutes = max(1, settings.web_login_token_ttl_seconds // 60)
    await message.answer(
        "🔗 Liên kết đăng nhập PR Admin của bạn:\n"
        f"{issued.url}\n\n"
        f"• Chỉ dùng được <b>một lần</b> và hết hạn sau <b>{minutes} phút</b>.\n"
        "• Muốn đăng nhập bằng Chrome/Safari: bạn <b>sao chép liên kết</b> rồi dán "
        "vào trình duyệt đó. Bấm thẳng ở đây sẽ mở trong trình duyệt của "
        "Telegram, và phiên đăng nhập chỉ nằm trong đó.\n"
        "• Đừng chuyển tiếp cho ai — ai mở liên kết này sẽ đăng nhập bằng tài "
        "khoản của bạn.\n"
        "• Cần liên kết mới thì gửi lại <code>/web</code> (liên kết cũ sẽ bị vô hiệu).",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


__all__: list[str] = ["router"]
