"""The "✅ Đã đọc" buttons attached to a published announcement.

**Why the acting user is not in the signature.** One group message is seen by
everybody in the group, so a single rendered keyboard physically cannot be
bound to one person - there is one message, not one per reader. The binding
therefore commits to the bot, the destination chat, the announcement and its
version, with a sentinel in place of the user.

That is not a weakening. Who pressed the button comes from the Telegram update
itself (``callback_query.from_user``), which the presser cannot forge and a
forwarded payload cannot change. Putting a user id *inside* the callback data
would be the weaker design: it would be a claim carried by the button, and this
is a fact carried by the platform.

The announcement's ``version`` is in the signature, so a button drawn for one
version of an announcement stops working if the announcement changes.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from aiogram.types import InlineKeyboardMarkup

from meobot.bot import formatting
from meobot.core.config import Settings
from meobot.core.time import utcnow
from meobot.domain.member.callbacks import MemberBinding, build

#: Stands in for "whoever is in this chat". A real Telegram id is never zero,
#: so this cannot collide with a user-bound button.
ANY_MEMBER = 0

ACK_LABEL = "✅ Đã đọc"
ASK_LABEL = "❓ Tôi cần hỏi thêm"


def receipt_binding(*, bot_id: int, chat_id: int) -> MemberBinding:
    """The binding a read-receipt button is signed against.

    Built the same way when rendering and when verifying, which is what makes
    a button forwarded to another chat fail: ``chat_id`` is part of the
    signature material and comes from the live update on the way back.
    """
    return MemberBinding(bot_id=bot_id, telegram_user_id=ANY_MEMBER, chat_id=chat_id)


def read_receipt_keyboard(
    *,
    announcement_id: uuid.UUID,
    settings: Settings,
    chat_id: int,
    bot_id: int = 0,
    version: int = 1,
    now: datetime | None = None,
) -> InlineKeyboardMarkup | None:
    """The two buttons under an announcement that asked for confirmation.

    Returns ``None`` when read receipts are switched off, so the caller does
    not have to check twice.
    """
    if not settings.announcement_read_receipt_enabled:
        return None

    expires_at = (now or utcnow()) + timedelta(
        seconds=settings.announcement_read_callback_ttl_seconds
    )
    binding = receipt_binding(bot_id=bot_id, chat_id=chat_id)

    def button(action: str) -> str:
        return build(
            action,
            secret=settings.callback_secret,
            binding=binding,
            expires_at=expires_at,
            entity_id=announcement_id,
            version=version,
        )

    return formatting.keyboard(
        [
            [(ACK_LABEL, button("announce.ack"))],
            [(ASK_LABEL, button("announce.ask"))],
        ]
    )
