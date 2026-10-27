"""The "🔄 Chạy lại" button under a failed-job alert (0077).

Signed (:mod:`meobot.domain.member.callbacks`) against the private chat it is
delivered to - the OWNER's own - and the incident it is about, so a button
forwarded to somebody else fails. The handler checks the presser is the OWNER
as well: the signature says where, the role says who may.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from aiogram.types import InlineKeyboardMarkup

from meobot.bot import formatting
from meobot.core.config import Settings
from meobot.core.time import utcnow
from meobot.domain.member.callbacks import MemberBinding, build

RERUN_ACTION = "cron.rerun"
RERUN_LABEL = "🔄 Chạy lại"
#: An alert read a few days later may still be acted on.
BUTTON_TTL = timedelta(days=7)


def rerun_binding(chat_id: int) -> MemberBinding:
    """A private chat: the chat id is the person's Telegram id."""
    return MemberBinding(bot_id=0, telegram_user_id=chat_id, chat_id=chat_id)


def rerun_keyboard(
    *, incident_id: uuid.UUID, chat_id: int, settings: Settings, now: datetime | None = None
) -> InlineKeyboardMarkup:
    data = build(
        RERUN_ACTION,
        secret=settings.callback_secret,
        binding=rerun_binding(chat_id),
        expires_at=(now or utcnow()) + BUTTON_TTL,
        entity_id=incident_id,
    )
    return formatting.keyboard([[(RERUN_LABEL, data)]])


__all__ = ["RERUN_ACTION", "RERUN_LABEL", "rerun_binding", "rerun_keyboard"]
