"""The only handler a Guest can reach.

This router is included **first**, and its single filter is "the access gate
bound a Guest principal to this update". That ordering is the containment: a
Guest's message is consumed here and never offered to the command routers or to
the free-text conversation router, so it cannot arrive at a handler that expects
an :class:`~meobot.domain.identity.models.Actor`.

What a Guest gets:

* plain chat generation, through
  :meth:`~meobot.application.conversation_service.ConversationService.handle_guest_message`,
  which is given no tool catalogue and consults no router;
* a deterministic refusal for anything operational, which costs no provider
  call and therefore costs the Guest none of their ten questions.

What a Guest never gets: a tool, a capability listing, an internal identifier,
a policy decision, or a reply in a private chat.
"""

from __future__ import annotations

import uuid

from aiogram import Router
from aiogram.filters import Filter
from aiogram.types import Message

from meobot.application.access_gate import GateResult
from meobot.application.conversation_service import GUEST_TOOL_DENIED, ConversationService
from meobot.application.quota_service import QuotaService
from meobot.bot import formatting
from meobot.bot.addressing import bot_username_of, strip_bot_mention
from meobot.core.config import Settings
from meobot.core.logging import get_logger
from meobot.db.session import Database
from meobot.domain.access.models import GuestPrincipal

logger = get_logger(__name__)

router = Router(name="guest")

GUEST_PROVIDER_DOWN = (
    "Mình đang gặp lỗi kết nối nên chưa trả lời được. Bạn thử lại sau một chút nhé."
)

GUEST_EMPTY_MESSAGE = "Mình đây. Bạn muốn hỏi gì?"

#: A Guest asking for internal data gets this, and pays nothing for it.
GUEST_DENIED = GUEST_TOOL_DENIED

MAX_GUEST_MESSAGE_LENGTH = 2000


class IsGuest(Filter):
    """Matches only updates the access gate authorised as a Guest.

    Applied to the whole router, so every handler added here inherits the
    containment rather than having to remember it. aiogram passes middleware
    data to filters as keyword arguments, which is how ``guest_principal`` -
    set by :class:`~meobot.bot.middlewares.AccessGateMiddleware` - gets here.
    """

    async def __call__(
        self, message: Message, guest_principal: GuestPrincipal | None = None
    ) -> bool:
        return guest_principal is not None


router.message.filter(IsGuest())


@router.message()
async def handle_guest_message(
    message: Message,
    request_id: uuid.UUID,
    conversation_service: ConversationService,
    settings: Settings,
    database: Database,
    guest_principal: GuestPrincipal,
    access: GateResult | None = None,
) -> None:
    """Answer a Guest, or refuse deterministically - never anything in between.

    ``guest_principal`` is a required parameter with no default, so aiogram only
    routes here when the gate bound one. A registered user's update never
    reaches this handler, and a Guest's update never reaches any other.
    """
    text = strip_bot_mention(message.text or "", bot_username_of(message)).strip()

    if message.chat.type == "private":
        # A Guest exists inside one group. Their grant means nothing here.
        await _release(database, settings, access)
        return

    if text.startswith("/") or not text:
        await _release(database, settings, access)
        await formatting.answer(
            message,
            formatting.escape(GUEST_DENIED if text.startswith("/") else GUEST_EMPTY_MESSAGE),
        )
        return

    if len(text) > MAX_GUEST_MESSAGE_LENGTH:
        await _release(database, settings, access)
        await formatting.answer(
            message,
            formatting.escape(
                "Tin nhắn quá dài. Bạn rút gọn dưới "
                f"{MAX_GUEST_MESSAGE_LENGTH} ký tự giúp mình nhé."
            ),
        )
        return

    await formatting.send_typing(message, enabled=settings.chat_typing_indicator)
    try:
        reply = await conversation_service.handle_guest_message(
            guest=guest_principal, message=text, request_id=request_id
        )
    except Exception:
        # A provider failure costs the Guest nothing: the slot goes back.
        logger.warning("guest_reply_failed", extra={"request_id": str(request_id)})
        await _release(database, settings, access)
        await formatting.answer(message, formatting.escape(GUEST_PROVIDER_DOWN))
        return

    try:
        await formatting.answer(message, formatting.render_assistant_text(reply.text))
    except Exception:  # pragma: no cover - answer() swallows its own failures
        logger.exception("guest_delivery_failed")
        await _release(database, settings, access)
        return

    await _commit(database, settings, access)


async def _commit(database: Database, settings: Settings, access: GateResult | None) -> None:
    """One delivered answer, one Guest question spent."""
    reservation = access.take_reservation() if access is not None else None
    if reservation is None:
        return
    async with database.transaction() as session:
        await QuotaService(session, settings).commit(reservation)


async def _release(database: Database, settings: Settings, access: GateResult | None) -> None:
    """Give the held question back - refusals and failures are free."""
    reservation = access.take_reservation() if access is not None else None
    if reservation is None:
        return
    async with database.transaction() as session:
        await QuotaService(session, settings).release(reservation)
