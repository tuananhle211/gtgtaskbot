"""Turning :class:`ButtonSpec` rows into signed Telegram keyboards.

The application layer describes buttons without knowing they are Telegram
buttons; this is the only place that knows how one is signed. Keeping the two
apart is what lets the interaction service be tested without aiogram, and what
guarantees no handler can render an *unsigned* button by accident - there is no
function here that produces one.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from aiogram.types import InlineKeyboardMarkup

from meobot.application.member_interaction_service import ButtonSpec
from meobot.bot import formatting
from meobot.core.config import Settings
from meobot.core.time import utcnow
from meobot.domain.member.callbacks import MemberBinding, build

#: How long a Member button stays pressable. Long enough to come back after a
#: meeting; short enough that a card from yesterday cannot act on today's data.
BUTTON_TTL = timedelta(hours=12)


def binding_for(*, bot_id: int, telegram_user_id: int, chat_id: int) -> MemberBinding:
    """The facts every button in this message is signed against."""
    return MemberBinding(bot_id=bot_id, telegram_user_id=telegram_user_id, chat_id=chat_id)


def render(
    rows: list[list[ButtonSpec]],
    *,
    settings: Settings,
    binding: MemberBinding,
    version: int = 0,
    now: datetime | None = None,
) -> InlineKeyboardMarkup | None:
    """Sign and lay out a keyboard, or ``None`` when there are no buttons."""
    if not rows:
        return None
    expires_at = (now or utcnow()) + BUTTON_TTL
    laid_out: list[list[tuple[str, str]]] = []
    for row in rows:
        rendered = [
            (
                spec.label,
                build(
                    spec.action,
                    secret=settings.callback_secret,
                    binding=binding,
                    expires_at=expires_at,
                    entity_id=_entity(spec.argument),
                    version=version,
                ),
            )
            for spec in row
        ]
        if rendered:
            laid_out.append(rendered)
    return formatting.keyboard(laid_out) if laid_out else None


def _entity(argument: str) -> uuid.UUID | None:
    """A button argument is either an entity id or nothing."""
    if not argument:
        return None
    try:
        return uuid.UUID(argument)
    except ValueError:
        return None
