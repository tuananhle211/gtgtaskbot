"""Free-text handler: everything that is not a slash command.

The handler decides nothing about *meaning*. It normalises the text, resolves
where the conversation is happening, and hands both to
:class:`~meobot.application.conversation_service.ConversationService`, which
runs route -> (chat | clarify | tool) and returns something to print.

What the handler does own is the transport contract:

* only the owner chats with the AI on Telegram (see
  :func:`~meobot.bot.commands.has_full_bot`). Anybody else is answered with
  one short pointer to the basic commands and ``/web`` - in a group only when
  they explicitly addressed MeoBot - and is never charged for it;
* a message starting with ``/`` never reaches here (the filter excludes it, and
  every command router is included before this one), so a slash command can
  never be swallowed by the natural-language path;
* ``@botusername`` mentions are stripped, so the same message works in a group
  and in a private chat;
* in a private chat no mention is needed; in a group MeoBot answers only when
  addressed or replied to, unless ``CHAT_GROUP_REQUIRES_MENTION`` is off;
* a "typing" indicator is sent before the provider is called, not after;
* model text is escaped and then rendered through the one safe Markdown-subset
  converter, so neither a stray ``<`` nor a raw ``**`` reaches a user;
* failures become a friendly Vietnamese sentence with a reference id, never a
  provider response body and never a traceback.

One sentence is deliberately absent from this file. The old code answered every
provider failure with *"Bạn thử nhắn lại ngắn gọn hơn"*. In reply to "Hello"
that is both wrong and impossible to act on: the provider failed, not the
message, and "Hello" does not get shorter. The recovery paths now live in the
service, and what is left here is the honest last resort.
"""

from __future__ import annotations

import uuid

from aiogram import F, Router
from aiogram.types import Message

from meobot.application.access_gate import GateResult
from meobot.application.conversation_service import ConversationService, ConversationTarget
from meobot.application.quota_service import QuotaService
from meobot.bot import formatting
from meobot.bot.addressing import (
    MENTION_PATTERN,
    bot_username_of,
    is_addressed_to_bot,
    strip_bot_mention,
)
from meobot.bot.commands import has_full_bot
from meobot.bot.texts import BASIC_ONLY_CHAT
from meobot.core.config import Settings
from meobot.core.errors import IntegrationTimeoutError, LLMError, MeoBotError
from meobot.core.logging import get_logger
from meobot.db.session import Database
from meobot.domain.identity.models import Actor

logger = get_logger(__name__)

router = Router(name="conversation")

#: Longer messages are refused rather than truncated - a truncated instruction
#: is a different instruction.
MAX_MESSAGE_LENGTH = 4000

TIMEOUT_REPLY = (
    "Mô hình AI trả lời lâu hơn bình thường nên mình dừng lại giữa chừng. Bạn "
    "nhắn lại giúp mình nhé — các lệnh vận hành vẫn chạy bình thường. "
    "Mã tham chiếu lỗi: {reference}."
)

#: Used only when every recovery path inside the service has already failed.
PROVIDER_DOWN_REPLY = (
    "Mình đang gặp lỗi kết nối với mô hình AI nên chưa thể trả lời đầy đủ. "
    "Các lệnh vận hành vẫn hoạt động. Mã tham chiếu lỗi: {reference}."
)


def short_reference(request_id: uuid.UUID) -> str:
    """A short, quotable id for one failed turn.

    The first block of the correlation id: long enough to find the log line,
    short enough to read off a phone screen into a message.
    """
    return str(request_id).split("-", 1)[0]


def _target_for(message: Message) -> ConversationTarget | None:
    """Where this conversation lives, for memory scoping."""
    if message.from_user is None or message.bot is None:
        return None
    return ConversationTarget(
        bot_id=message.bot.id,
        chat_id=message.chat.id,
        telegram_user_id=message.from_user.id,
    )


@router.message(F.text & ~F.text.startswith("/"))
async def handle_free_text(
    message: Message,
    actor: Actor,
    request_id: uuid.UUID,
    conversation_service: ConversationService,
    settings: Settings,
    database: Database,
    access: GateResult | None = None,
) -> None:
    """Route a natural-language message through the safe decision pipeline.

    ``access`` carries whatever the access gate held on this turn's behalf - for
    a metered Member, one reserved chat slot. The slot is settled here and only
    here, because this is the first point at which it is known whether Telegram
    actually accepted an answer:

    * a provider failure, a refusal, or a send that Telegram rejects releases
      it, and the member is charged nothing;
    * a delivered answer commits it, and one message costs exactly one unit
      however many questions it contained.
    """
    raw = message.text or ""
    if not has_full_bot(actor.role):
        # The basic bot has no AI chat, so nothing below may run: no provider
        # call, no tool, no slot spent. In a group, silence unless addressed by
        # name or reply - even when the deployment does not require a mention.
        await _release(database, settings, access)
        if is_addressed_to_bot(message, require_mention=True):
            await formatting.answer(message, formatting.escape(BASIC_ONLY_CHAT))
        return

    if not is_addressed_to_bot(message, require_mention=settings.chat_group_requires_mention):
        await _release(database, settings, access)
        return

    text = strip_bot_mention(raw, bot_username_of(message)).strip()

    if not text:
        # A bare mention with no request. Say something useful rather than
        # silently dropping the message - and charge nothing for it.
        await _release(database, settings, access)
        if MENTION_PATTERN.search(raw):
            await formatting.answer(
                message, "Mình đây. Bạn cần mình làm gì? Gõ /help để xem danh sách nhé."
            )
        return

    if len(text) > MAX_MESSAGE_LENGTH:
        await _release(database, settings, access)
        await formatting.answer(
            message,
            formatting.escape(
                f"Tin nhắn quá dài ({len(text)} ký tự). Vui lòng rút gọn dưới "
                f"{MAX_MESSAGE_LENGTH} ký tự."
            ),
        )
        return

    # Sent before the provider is called: the indicator is only useful while
    # the user is waiting, which is now.
    await formatting.send_typing(message, enabled=settings.chat_typing_indicator)
    reference = short_reference(request_id)

    try:
        reply = await conversation_service.handle_message(
            actor=actor,
            message=text,
            request_id=request_id,
            target=_target_for(message),
        )
    except IntegrationTimeoutError:
        logger.warning("conversation_timeout", extra={"request_id": str(request_id)})
        await _release(database, settings, access)
        await formatting.answer(
            message, formatting.escape(TIMEOUT_REPLY.format(reference=reference))
        )
        return
    except LLMError as exc:
        # The provider's response body is never shown: it can echo the prompt,
        # and a stack trace helps nobody in a Telegram chat.
        logger.warning(
            "conversation_llm_error",
            extra={"error_code": exc.code, "request_id": str(request_id)},
        )
        await _release(database, settings, access)
        await formatting.answer(
            message, formatting.escape(PROVIDER_DOWN_REPLY.format(reference=reference))
        )
        return
    except MeoBotError as exc:
        logger.warning(
            "conversation_failed",
            extra={"error_code": exc.code, "request_id": str(request_id)},
        )
        await _release(database, settings, access)
        await formatting.answer(message, "⛔ " + formatting.escape(exc.message))
        return
    except Exception:
        # Unexpected failure: log with traceback, tell the user something
        # neutral and actionable, and let the update finish.
        logger.exception("conversation_unexpected_error", extra={"request_id": str(request_id)})
        await _release(database, settings, access)
        await formatting.answer(
            message, formatting.escape(PROVIDER_DOWN_REPLY.format(reference=reference))
        )
        return

    # Model text is escaped first and only then given the small set of tags
    # MeoBot chose - see ``formatting.render_assistant_text``. An unmatched "<"
    # in a generated sentence would otherwise be an unterminated HTML entity and
    # Telegram would reject the whole message; a raw "**" would simply be shown
    # to the user, which is the bug this release fixed.
    delivered = await _deliver(message, reply.text)
    if delivered and reply.executed_a_tool:
        # A turn that ran a tool is operational work, not conversation. The
        # member keeps their chat slot.
        await _release(database, settings, access)
    elif delivered:
        await _commit(database, settings, access)
    else:
        await _release(database, settings, access)


async def _deliver(message: Message, text: str) -> bool:
    """Send the answer; report whether Telegram accepted it.

    ``formatting.answer`` never raises - it degrades and logs - so "did this
    reach the user" has to be asked separately from "did this raise".
    """
    try:
        await formatting.answer(message, formatting.render_assistant_text(text))
    except Exception:  # pragma: no cover - answer() already swallows its own
        logger.exception("conversation_delivery_failed")
        return False
    return True


async def _commit(database: Database, settings: Settings, access: GateResult | None) -> None:
    """Convert a held chat slot into a used one. At most once per turn."""
    reservation = access.take_reservation() if access is not None else None
    if reservation is None:
        return
    async with database.transaction() as session:
        await QuotaService(session, settings).commit(reservation)


async def _release(database: Database, settings: Settings, access: GateResult | None) -> None:
    """Give a held chat slot back. Safe to call more than once per turn."""
    reservation = access.take_reservation() if access is not None else None
    if reservation is None:
        return
    async with database.transaction() as session:
        await QuotaService(session, settings).release(reservation)
