"""The Vietnamese-first entry point: natural text and buttons for a Member.

This router is included **before** the free-text conversation router and
**after** the command routers. It answers only what it recognises
deterministically - :mod:`~meobot.domain.member.intents` decides - and lets
everything else fall through to ordinary conversation.

That fall-through is the whole design. An unrecognised message is *not*
refused; it is treated as chat, which is the only branch that costs a chat
slot. So the worst case of a missing pattern is "the Member spent one AI
response on something we could have answered for free", never "MeoBot refused
to help".

Handlers here stay thin on purpose: parse the update, resolve the actor, call
:class:`~meobot.application.member_interaction_service.MemberInteractionService`,
render, send. No business logic, no SQL, no Vietnamese string literals.
"""

from __future__ import annotations

import uuid

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from meobot.application.member_interaction_service import (
    MemberInteractionService,
    MemberReply,
)
from meobot.application.member_list_service import MemberListService
from meobot.bot import formatting, member_keyboards
from meobot.bot.commands import has_full_bot
from meobot.bot.member_filters import MemberIntentFilter
from meobot.bot.texts import BASIC_ONLY_CHAT
from meobot.core.config import Settings
from meobot.core.errors import MeoBotError
from meobot.core.logging import get_logger
from meobot.core.time import utcnow
from meobot.db.session import Database
from meobot.domain.identity.models import Actor
from meobot.domain.member import copy
from meobot.domain.member.callbacks import data_pattern, parse
from meobot.domain.member.intents import MemberIntent
from meobot.domain.permissions.matrix import Permission, has_permission

logger = get_logger(__name__)

router = Router(name="member")

#: Intents this router answers itself. Anything else - including
#: :attr:`MemberIntent.GENERATIVE` - is left for the conversation router.
HANDLED: frozenset[MemberIntent] = frozenset(
    {
        MemberIntent.HOME,
        MemberIntent.HELP,
        MemberIntent.VIEW_AI_ALLOWANCE,
        MemberIntent.VIEW_MY_HR_REQUESTS,
        MemberIntent.VIEW_MY_HR_STATS,
        MemberIntent.VIEW_PENDING_HR,
        MemberIntent.VIEW_WHO_IS_OFF,
        MemberIntent.VIEW_HR_REPORT,
        MemberIntent.VIEW_WORK,
        MemberIntent.ACCEPT_WORK,
        MemberIntent.UPDATE_PROGRESS,
        MemberIntent.COMPLETE_WORK,
        MemberIntent.SUBMIT_PROOF,
        MemberIntent.REPORT_ISSUE,
        MemberIntent.VIEW_CHANNELS,
        MemberIntent.VIEW_SCHEDULE_GAPS,
        MemberIntent.VIEW_PERSONAL_REPORT,
    }
)

#: Buttons this router answers. HR *flow* buttons live in the HR router.
_ACTION_INTENTS: dict[str, MemberIntent] = {
    "home.open": MemberIntent.HOME,
    "help.open": MemberIntent.HELP,
    "quota.mine": MemberIntent.VIEW_AI_ALLOWANCE,
    "work.list": MemberIntent.VIEW_WORK,
    "work.progress": MemberIntent.VIEW_PERSONAL_REPORT,
    "hr.mine": MemberIntent.VIEW_MY_HR_REQUESTS,
    "hr.stats": MemberIntent.VIEW_MY_HR_STATS,
    "hr.pending": MemberIntent.VIEW_PENDING_HR,
    "hr.today": MemberIntent.VIEW_WHO_IS_OFF,
}

#: Only these codes reach this router; the HR router owns its own.
MEMBER_BUTTON_PATTERN = data_pattern(_ACTION_INTENTS)


async def send(
    message: Message,
    reply: MemberReply,
    *,
    settings: Settings,
    database: Database,
    actor: Actor,
) -> None:
    """Render one :class:`MemberReply` and remember any list it showed.

    The list is recorded *after* a successful send, keyed to this person in this
    chat, so "việc số 2" can only ever refer to something they were actually
    shown.
    """
    if message.bot is None or message.from_user is None:  # pragma: no cover
        return
    binding = member_keyboards.binding_for(
        bot_id=message.bot.id,
        telegram_user_id=message.from_user.id,
        chat_id=message.chat.id,
    )

    version = 0
    if reply.list_kind and reply.list_item_ids:
        async with database.transaction() as session:
            version = await MemberListService(session, settings).remember(
                bot_id=message.bot.id,
                chat_id=message.chat.id,
                telegram_user_id=message.from_user.id,
                kind=reply.list_kind,
                item_ids=reply.list_item_ids,
            )

    await formatting.answer(
        message,
        formatting.render_assistant_text(reply.text),
        reply_markup=member_keyboards.render(
            reply.buttons, settings=settings, binding=binding, version=version
        ),
    )


async def _dispatch(
    intent: MemberIntent,
    *,
    actor: Actor,
    settings: Settings,
    database: Database,
) -> MemberReply | None:
    """Turn an intent into a reply, or ``None`` to let it fall through."""
    async with database.transaction() as session:
        service = MemberInteractionService(session, settings)

        if intent is MemberIntent.HOME:
            return await service.home(actor)
        if intent is MemberIntent.HELP:
            return service.help(actor)
        if intent is MemberIntent.VIEW_AI_ALLOWANCE:
            if not has_full_bot(actor.role):
                # No AI chat on the basic bot, so no allowance to report.
                return MemberReply(text=BASIC_ONLY_CHAT)
            return await service.ai_allowance(actor)
        if intent is MemberIntent.VIEW_MY_HR_REQUESTS:
            return await service.my_hr_requests(actor)
        if intent is MemberIntent.VIEW_MY_HR_STATS:
            return await service.my_hr_statistics(actor)
        if intent is MemberIntent.VIEW_PENDING_HR:
            return await service.pending_hr_requests(actor)
        if intent is MemberIntent.VIEW_WHO_IS_OFF:
            return await service.absence_today(actor)
        if intent is MemberIntent.VIEW_HR_REPORT:
            return await service.department_report(actor)
        # Understood, but the module behind it does not exist. Say so plainly
        # rather than inventing a result.
        return service.not_built_yet(intent)


@router.message(MemberIntentFilter(HANDLED))
async def handle_member_text(
    message: Message,
    actor: Actor,
    settings: Settings,
    database: Database,
    member_intent: MemberIntent,
) -> None:
    """Answer a recognised Vietnamese request.

    The filter has already decided this message is ours. Anything it does not
    recognise never reaches this handler and carries on to the conversation
    router - which is the only branch that spends a chat slot.
    """
    try:
        reply = await _dispatch(member_intent, actor=actor, settings=settings, database=database)
    except MeoBotError as exc:
        await formatting.answer(message, formatting.escape(exc.message))
        return
    except Exception:
        logger.exception("member_intent_failed", extra={"intent": member_intent.value})
        await formatting.answer(message, formatting.escape(copy.Problem.SOMETHING_WENT_WRONG.value))
        return

    if reply is None:
        return
    await send(message, reply, settings=settings, database=database, actor=actor)


@router.callback_query(F.data.regexp(MEMBER_BUTTON_PATTERN))
async def handle_member_button(
    query: CallbackQuery,
    actor: Actor,
    settings: Settings,
    database: Database,
    request_id: uuid.UUID,
) -> None:
    """Handle a signed Member button.

    Three refusals before anything happens, and each says something different
    so the person knows what to do: not yours, expired, or no longer valid.
    """
    await query.answer()
    if query.message is None or query.bot is None or query.data is None:  # pragma: no cover
        return

    binding = member_keyboards.binding_for(
        bot_id=query.bot.id,
        telegram_user_id=query.from_user.id,
        chat_id=query.message.chat.id,
    )
    payload = parse(query.data, secret=settings.callback_secret, binding=binding)
    if payload is None:
        # Signed for a different person, chat or bot - or not signed at all.
        await query.answer(copy.Problem.BUTTON_NOT_YOURS.value, show_alert=True)
        return
    if payload.is_expired(utcnow()):
        await formatting.edit_callback(query, formatting.escape(copy.Problem.BUTTON_EXPIRED.value))
        return

    intent = _ACTION_INTENTS[payload.action]

    try:
        reply = await _dispatch(intent, actor=actor, settings=settings, database=database)
    except MeoBotError as exc:
        await formatting.answer_callback(query, formatting.escape(exc.message))
        return

    if reply is None or not isinstance(query.message, Message):
        return
    await send(query.message, reply, settings=settings, database=database, actor=actor)


def member_can(actor: Actor, permission: Permission) -> bool:
    """Small helper so handlers never import the matrix directly."""
    return has_permission(actor.role, permission)
