"""Reminders in natural Vietnamese: preview, confirm, manage.

The shape of every path here is the same, and it is the fix for the bug that
opened this release: **parse → preview → confirm → commit.** MeoBot used to
answer "em đã ghi nhận lịch nhắc" to a sentence it had merely understood. Now
the only place that says "đã tạo" runs after the transaction committed, and the
preview says "Bạn đang tạo" instead.

No model is involved. Recognising the message, reading the schedule out of it
and computing the next occurrence are all deterministic, so a reminder costs a
Member none of their daily AI allowance - and works when the provider is down.

One clarifying question, at most. "4 giờ" does not say which half of the day it
means, and a wrong guess puts the reminder twelve hours out, so it asks. Every
other supported phrasing resolves without asking.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import time, timedelta

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from meobot.application.reminder_service import ReminderService
from meobot.bot import formatting
from meobot.bot.member_filters import InMemberFlow, MemberIntentFilter
from meobot.core.config import Settings
from meobot.core.errors import MeoBotError
from meobot.core.logging import get_logger
from meobot.core.time import utcnow
from meobot.db.models.reminder import Reminder
from meobot.db.session import Database
from meobot.domain.identity.models import Actor
from meobot.domain.member.callbacks import MemberBinding, data_pattern, parse
from meobot.domain.member.intents import MemberIntent, classify
from meobot.domain.member.normalization import strip_accents
from meobot.domain.reminders.draft import (
    DRAFT_KEY,
    MISSING_CONTENT,
    ReminderDraftState,
)
from meobot.domain.reminders.models import ScheduleKind, status_label
from meobot.domain.reminders.parsing import ReminderDraft, parse_reminder
from meobot.domain.reminders.schedule import ReminderSchedule, describe, describe_instant

logger = get_logger(__name__)

router = Router(name="reminders")

#: Intents this router owns. Everything else falls through untouched.
HANDLED: frozenset[MemberIntent] = frozenset(
    {
        MemberIntent.CREATE_REMINDER,
        MemberIntent.VIEW_REMINDERS,
        MemberIntent.NEXT_REMINDER,
        MemberIntent.PAUSE_REMINDER,
        MemberIntent.RESUME_REMINDER,
        MemberIntent.CANCEL_REMINDER,
        MemberIntent.EDIT_REMINDER,
    }
)

_ACTIONS = (
    "rem.confirm",
    "rem.edit",
    "rem.cancel",
    "rem.hour.am",
    "rem.hour.pm",
    "rem.pause",
    "rem.resume",
    "rem.stop",
)

# --- Copy. Every user-visible sentence lives here, in one place. -----------
PREVIEW_HEADING = "⏰ LỊCH NHẮC"
CONFIRM_BUTTON = "✅ Tạo lịch nhắc"
EDIT_BUTTON = "✏️ Sửa lại"
CANCEL_BUTTON = "❌ Huỷ"

#: Said *before* anything is written. Never "đã".
PREVIEW_FOOTER = "Bạn kiểm tra lại giúp mình trước khi xác nhận nhé."

#: Said *after* the row is committed, and only then.
CREATED = "✅ TasksBot đã tạo lịch nhắc."

CANCELLED_DRAFT = "TasksBot chưa tạo lịch nhắc nào. Bạn nhắn lại khi cần nhé."

#: Shown when the account is not yet usable. Deliberately says "tài khoản",
#: not "hồ sơ": in this codebase "hồ sơ" is the optional descriptive
#: ActorProfile, which a reminder does not need and never did. Telling somebody
#: their profile is missing sends them looking for a thing that is not the
#: problem.
ACCOUNT_NOT_READY = (
    "TasksBot cần hoàn tất tài khoản sử dụng trước khi tạo lịch nhắc.\nBạn nhấn /start nhé."
)

#: After /start, when the held request could be resumed.
RESUMED_AFTER_START = "TasksBot đã hoàn tất tài khoản của bạn."

#: After /start, when it could not.
RESUME_FAILED = (
    "TasksBot đã hoàn tất tài khoản của bạn.\n"
    "Yêu cầu lịch nhắc trước đó chưa được lưu, bạn gửi lại giúp mình nhé."
)

#: What MeoBot says while it is waiting for one missing field. Naming the draft
#: is the point: the old behaviour repeated the same generic error with no hint
#: that a half-built reminder was still open.
WAITING_FOR_TIME = (
    "TasksBot đang chờ thời gian cho lịch nhắc “{content}”.\n\n"
    "Bạn có thể gửi:\n"
    "• 22h40\n"
    "• Sau 5 phút\n"
    "• 4 giờ chiều"
)
WAITING_FOR_CONTENT = "TasksBot đã ghi thời gian {timing}.\n\nBạn muốn TasksBot nhắc nội dung gì ạ?"
CANCEL_DRAFT_BUTTON = "❌ Huỷ lịch nhắc"

#: Natural cancellation, folded and accent-free.
_CANCEL_WORDS: frozenset[str] = frozenset(
    {"huy", "thoi", "khong tao nua", "bo lich nay", "khong can nua", "bo qua", "dung lai"}
)
EDIT_PROMPT = "Bạn nhắn lại nội dung và thời gian mới giúp mình nhé."
AMBIGUOUS_HOUR = "Bạn muốn nhắc lúc mấy giờ?"
NO_REMINDERS = "Bạn chưa có lịch nhắc nào. Bạn nhắn ví dụ “nhắc tôi 8 giờ sáng mai họp” nhé."
WHICH_REMINDER = "Bạn muốn chọn lịch nhắc nào?"
PAUSED = "⏸ TasksBot đã tạm dừng lịch nhắc này."
RESUMED = "▶️ TasksBot đã bật lại lịch nhắc này."
STOPPED = "🛑 TasksBot đã huỷ lịch nhắc này."


class ReminderFlow(StatesGroup):
    """The two moments a reminder draft has to survive a round trip."""

    confirming = State()
    choosing_hour = State()


def _binding(message: Message) -> MemberBinding:
    return MemberBinding(
        bot_id=message.bot.id if message.bot is not None else 0,
        telegram_user_id=message.from_user.id if message.from_user is not None else 0,
        chat_id=message.chat.id,
    )


def _binding_of_query(query: CallbackQuery) -> MemberBinding:
    chat_id = query.message.chat.id if isinstance(query.message, Message) else 0
    return MemberBinding(
        bot_id=query.bot.id if query.bot is not None else 0,
        telegram_user_id=query.from_user.id,
        chat_id=chat_id,
    )


def _keyboard(
    rows: list[list[tuple[str, str]]],
    *,
    settings: Settings,
    binding: MemberBinding,
    entity_id: uuid.UUID | None = None,
    version: int = 0,
) -> InlineKeyboardMarkup:
    """Sign each button against who may press it and where."""
    from meobot.domain.member.callbacks import build

    expires_at = utcnow() + timedelta(seconds=settings.member_flow_ttl_seconds)
    return formatting.keyboard(
        [
            [
                (
                    label,
                    build(
                        action,
                        secret=settings.callback_secret,
                        binding=binding,
                        expires_at=expires_at,
                        entity_id=entity_id,
                        version=version,
                    ),
                )
                for label, action in row
            ]
            for row in rows
        ]
    )


# --- Creating ---------------------------------------------------------------
@router.message(MemberIntentFilter(HANDLED))
async def handle_reminder_intent(
    message: Message,
    actor: Actor,
    database: Database,
    settings: Settings,
    state: FSMContext,
    member_intent: MemberIntent,
    member_text: str,
) -> None:
    """Route one recognised reminder message."""
    if member_intent is MemberIntent.CREATE_REMINDER:
        await _start_creation(message, actor, database, settings, state, member_text)
        return
    if member_intent in {MemberIntent.VIEW_REMINDERS, MemberIntent.NEXT_REMINDER}:
        await _list(message, actor, database, settings)
        return
    await _manage(message, actor, database, settings, member_intent, member_text)


async def _start_creation(
    message: Message,
    actor: Actor,
    database: Database,
    settings: Settings,
    state: FSMContext,
    text: str,
    *,
    existing: ReminderDraftState | None = None,
) -> None:
    """Parse, merge into any draft already open, and advance.

    ``existing`` is what makes a follow-up a *follow-up*. Without it, "22h40"
    sent to answer "what time?" was parsed as an entire new reminder - which
    discarded the content the person had already given and asked the same
    question again.
    """
    now = utcnow()
    result = parse_reminder(text, now=now, tz=settings.reminder_timezone)

    # A form-of-address request rides along with plenty of real messages. It is
    # a separate, durable preference; honouring it must not stop the reminder.
    if result.address_preference:
        await _remember_address(actor, database, settings, result.address_preference)

    if result.problem:
        if existing is not None and MISSING_CONTENT in existing.missing:
            # The draft already knows *when*; whatever was just typed is
            # *what*. This is the only case where an unparseable reply is
            # allowed to fill a field, and it fills exactly one.
            await _advance(
                message,
                actor,
                database,
                settings,
                state,
                existing.with_content(text),
            )
            return
        if existing is not None:
            # A draft is open, waiting for a time, and this reply did not give
            # one. Say what is still needed rather than repeating a generic
            # parse failure - and leave every field of the draft untouched.
            await _ask_for_missing(message, settings, existing)
            return
        if result.partial_content:
            # "Nhắc tôi đi ngủ": a real request with one thing missing. Open a
            # draft and ask for the time, rather than refusing the whole thing.
            await _advance(
                message,
                actor,
                database,
                settings,
                state,
                ReminderDraftState(
                    content=result.partial_content,
                    original_text=text,
                    source_chat_id=message.chat.id,
                    initiator_telegram_id=message.from_user.id if message.from_user else None,
                    timezone_name=str(settings.reminder_timezone),
                    created_at=now,
                ),
            )
            return
        await formatting.answer(message, formatting.escape(result.problem))
        return

    if result.needs_question:
        # One question, with the two readings as buttons - "sáng hay chiều" is
        # faster to tap than to type, and leaves no room for a second mistake.
        await state.set_state(ReminderFlow.choosing_hour)
        await state.update_data(reminder_text=text, ambiguous_hour=result.ambiguous_hour)
        hour = result.ambiguous_hour or 0
        await formatting.answer(
            message,
            formatting.escape(AMBIGUOUS_HOUR),
            reply_markup=_keyboard(
                [
                    [(f"{hour:02d}:00 sáng", "rem.hour.am")],
                    [(f"{hour + 12:02d}:00 chiều", "rem.hour.pm")],
                    [(CANCEL_BUTTON, "rem.cancel")],
                ],
                settings=settings,
                binding=_binding(message),
            ),
        )
        return

    if result.draft is None:  # pragma: no cover - guarded by ``problem``
        return

    parsed = result.draft
    base = existing or ReminderDraftState(
        original_text=text,
        source_chat_id=message.chat.id,
        initiator_telegram_id=message.from_user.id if message.from_user else None,
        timezone_name=str(settings.reminder_timezone),
        created_at=now,
    )
    # Merge field by field. A follow-up fills what is missing; it never
    # overwrites something the person already told us.
    merged = base.with_schedule(parsed.schedule, relative_minutes=result.relative_minutes)
    if parsed.content:
        merged = merged.with_content(parsed.content)
    elif existing is None:
        merged = merged.with_content("")
    await _advance(message, actor, database, settings, state, merged)


async def _remember_address(
    actor: Actor, database: Database, settings: Settings, address: str
) -> None:
    """Store how this person wants to be addressed, if the account allows it.

    Best effort on purpose: a preference that cannot be saved must not take the
    reminder down with it. An account that does not exist yet simply has
    nowhere to keep this, and the reminder is the thing that was asked for.
    """
    if actor.user_id is None:
        return
    from meobot.application.actor_profile_service import ActorProfileService

    try:
        async with database.transaction() as session:
            await ActorProfileService(session, settings).set_preferred_address(actor, address)
    except Exception:
        logger.info("address_preference_not_stored", extra={"length": len(address)})


async def _advance(
    message: Message,
    actor: Actor,
    database: Database,
    settings: Settings,
    state: FSMContext,
    draft: ReminderDraftState,
) -> None:
    """Ask for the next missing field, or show the preview when none is.

    The single place a draft is stored, so "what is still missing" and "what
    is shown" can never disagree.
    """
    await state.set_state(ReminderFlow.confirming)
    await state.update_data({DRAFT_KEY: draft.to_dict()})

    if not draft.is_complete:
        await _ask_for_missing(message, settings, draft)
        return

    if actor.user_id is None:
        # The account is not usable yet. Park the draft rather than losing it:
        # /start will pick it up and carry on where this left off.
        await state.update_data({DRAFT_KEY: replace(draft, awaiting_onboarding=True).to_dict()})
        await formatting.answer(message, formatting.escape(ACCOUNT_NOT_READY))
        return

    await _render_preview(message, actor, database, settings, draft)


async def _ask_for_missing(message: Message, settings: Settings, draft: ReminderDraftState) -> None:
    """One question about one missing field, with examples and a way out."""
    if MISSING_CONTENT in draft.missing:
        body = WAITING_FOR_CONTENT.format(timing=draft.describe_timing())
    else:
        body = WAITING_FOR_TIME.format(content=draft.content)
    await formatting.answer(
        message,
        formatting.escape(body),
        reply_markup=_keyboard(
            [[(CANCEL_DRAFT_BUTTON, "rem.cancel")]],
            settings=settings,
            binding=_binding(message),
        ),
    )


async def _render_preview(
    message: Message,
    actor: Actor,
    database: Database,
    settings: Settings,
    draft: ReminderDraftState,
) -> None:
    """Show the completed draft. Still nothing durable exists."""
    now = utcnow()
    tz = settings.reminder_timezone
    async with database.session() as session:
        try:
            preview = await ReminderService(session, settings).preview_for_draft(
                actor=actor,
                draft=ReminderDraft(
                    content=draft.content, schedule=draft.to_schedule(now=now, tz=tz)
                ),
                now=now,
            )
        except MeoBotError as exc:
            await formatting.answer(message, "⛔ " + formatting.escape(exc.message))
            return

    await formatting.answer(
        message,
        formatting.escape(preview.render(tz, timing=draft.describe_timing()))
        + "\n\n"
        + formatting.escape(PREVIEW_FOOTER),
        reply_markup=_keyboard(
            [
                [(CONFIRM_BUTTON, "rem.confirm")],
                [(EDIT_BUTTON, "rem.edit")],
                [(CANCEL_BUTTON, "rem.cancel")],
            ],
            settings=settings,
            binding=_binding(message),
        ),
    )


@router.callback_query(F.data.regexp(data_pattern(_ACTIONS)))
async def handle_reminder_button(
    query: CallbackQuery,
    actor: Actor,
    database: Database,
    settings: Settings,
    state: FSMContext,
) -> None:
    """Apply one signed reminder button."""
    await query.answer()
    payload = parse(
        query.data or "", secret=settings.callback_secret, binding=_binding_of_query(query)
    )
    if payload is None or payload.is_expired(utcnow()):
        await formatting.edit_callback(query, formatting.escape("Nút này không còn hiệu lực."))
        return

    if payload.action == "rem.cancel":
        await state.clear()
        await formatting.edit_callback(query, formatting.escape(CANCELLED_DRAFT))
        return
    if payload.action == "rem.edit":
        await state.clear()
        await formatting.edit_callback(query, formatting.escape(EDIT_PROMPT))
        return
    if payload.action in {"rem.hour.am", "rem.hour.pm"}:
        await _resolve_hour(query, actor, database, settings, state, payload.action)
        return
    if payload.action == "rem.confirm":
        await _commit(query, actor, database, settings, state)
        return
    await _apply_management(query, actor, database, settings, payload.action, payload.entity_id)


async def _resolve_hour(
    query: CallbackQuery,
    actor: Actor,
    database: Database,
    settings: Settings,
    state: FSMContext,
    action: str,
) -> None:
    """Re-parse the original sentence with the ambiguity settled."""
    data = await state.get_data()
    text = str(data.get("reminder_text") or "")
    hour = int(data.get("ambiguous_hour") or 0)
    if not text:
        await formatting.edit_callback(query, formatting.escape(CANCELLED_DRAFT))
        return

    now = utcnow()
    result = parse_reminder(text, now=now, tz=settings.reminder_timezone)
    resolved_hour = (hour if action == "rem.hour.am" else hour + 12) % 24

    # Only the hour was ever in doubt, so only the hour is replaced - the shape
    # (one-time, daily, weekly) and the content stay as they were understood.
    base = result.draft.schedule if result.draft is not None else _reparse_shape(text, settings)
    schedule = ReminderSchedule(
        kind=base.kind,
        local_time=time(hour=resolved_hour, minute=base.local_time.minute),
        weekday=base.weekday,
        run_date=base.run_date,
    )
    held = await _load_draft(state, settings) or ReminderDraftState(
        original_text=text,
        source_chat_id=query.message.chat.id if isinstance(query.message, Message) else None,
        initiator_telegram_id=query.from_user.id,
        timezone_name=str(settings.reminder_timezone),
        created_at=now,
    )
    merged = held.with_schedule(schedule)
    if result.draft is not None and result.draft.content:
        merged = merged.with_content(result.draft.content)

    if isinstance(query.message, Message):
        await _advance(query.message, actor, database, settings, state, merged)


def _reparse_shape(text: str, settings: Settings) -> ReminderSchedule:
    """A last-resort shape when re-parsing an amended sentence found none."""
    return ReminderSchedule(
        kind=ScheduleKind.ONE_TIME,
        local_time=time(hour=0),
        run_date=utcnow().astimezone(settings.reminder_timezone).date(),
    )


async def _commit(
    query: CallbackQuery,
    actor: Actor,
    database: Database,
    settings: Settings,
    state: FSMContext,
) -> None:
    """Write the reminder. **The first and only place that says "đã tạo".**"""
    held = await _load_draft(state, settings)
    if held is None or not held.is_complete:
        await formatting.edit_callback(query, formatting.escape(CANCELLED_DRAFT))
        return

    now = utcnow()
    draft = ReminderDraft(
        content=held.content,
        schedule=held.to_schedule(now=now, tz=settings.reminder_timezone),
    )
    async with database.transaction() as session:
        service = ReminderService(session, settings)
        try:
            preview = await service.preview_for_draft(actor=actor, draft=draft, now=now)
            reminder = await service.create(
                actor=actor,
                preview=preview,
                source_chat_id=(
                    query.message.chat.id if isinstance(query.message, Message) else None
                ),
            )
        except MeoBotError as exc:
            await formatting.edit_callback(query, "⛔ " + formatting.escape(exc.message))
            return
        summary = _created_summary(reminder, settings)

    await state.clear()
    await formatting.edit_callback(query, summary)


def _created_summary(reminder: Reminder, settings: Settings) -> str:
    """The confirmation, written only after the row exists."""
    tz = settings.reminder_timezone
    schedule = _schedule_of(reminder)
    lines = [
        formatting.escape(CREATED),
        "",
        formatting.escape(f"• Nội dung: {reminder.content}"),
        formatting.escape(f"• Lặp lại: {_repeat_text(reminder, schedule)}"),
    ]
    if reminder.next_run_at is not None:
        lines.append(
            formatting.escape(f"• Lần tiếp theo: {describe_instant(reminder.next_run_at, tz=tz)}")
        )
    return "\n".join(lines)


def _repeat_text(reminder: Reminder, schedule: ReminderSchedule) -> str:
    if reminder.schedule_kind is ScheduleKind.ONE_TIME:
        return "Một lần"
    return describe(schedule).replace("mỗi", "Mỗi", 1)


# --- Managing ---------------------------------------------------------------
async def _list(message: Message, actor: Actor, database: Database, settings: Settings) -> None:
    """ "Lịch nhắc của tôi." """
    if actor.user_id is None:
        await formatting.answer(message, formatting.escape(NO_REMINDERS))
        return
    async with database.session() as session:
        rows = list(await ReminderService(session, settings).list_for(user_id=actor.user_id))

    if not rows:
        await formatting.answer(message, formatting.escape(NO_REMINDERS))
        return

    tz = settings.reminder_timezone
    lines = ["⏰ " + formatting.bold("LỊCH NHẮC CỦA BẠN"), ""]
    for index, row in enumerate(rows, start=1):
        lines.append(formatting.escape(f"{index}. {row.content}"))
        lines.append(
            formatting.escape(f"   {describe(_schedule_of(row))} · {status_label(row.status)}")
        )
        if row.next_run_at is not None:
            lines.append(
                formatting.escape(f"   Lần tới: {describe_instant(row.next_run_at, tz=tz)}")
            )
    await formatting.answer(message, "\n".join(lines))


def _schedule_of(reminder: Reminder) -> ReminderSchedule:
    from meobot.domain.reminders.schedule import parse_recurrence_rule

    return parse_recurrence_rule(reminder.recurrence_rule, local_time=reminder.local_time)


async def _manage(
    message: Message,
    actor: Actor,
    database: Database,
    settings: Settings,
    intent: MemberIntent,
    text: str,
) -> None:
    """Pause, resume or cancel - asking one question when several match."""
    if actor.user_id is None:
        await formatting.answer(message, formatting.escape(NO_REMINDERS))
        return

    action = {
        MemberIntent.PAUSE_REMINDER: "rem.pause",
        MemberIntent.RESUME_REMINDER: "rem.resume",
        MemberIntent.CANCEL_REMINDER: "rem.stop",
        MemberIntent.EDIT_REMINDER: "rem.stop",
    }[intent]

    async with database.session() as session:
        service = ReminderService(session, settings)
        matches = list(await service.find(user_id=actor.user_id, phrase=_subject_of(text)))
        if not matches:
            matches = list(await service.list_for(user_id=actor.user_id))

    if not matches:
        await formatting.answer(message, formatting.escape(NO_REMINDERS))
        return

    if len(matches) == 1:
        await _apply_to(message, actor, database, settings, action, matches[0].id)
        return

    # Several could be meant. Ask once rather than guessing: pausing the wrong
    # reminder is silent, and is only discovered by the right one not firing.
    await formatting.answer(
        message,
        formatting.escape(WHICH_REMINDER),
        reply_markup=formatting.keyboard(
            [
                [
                    (
                        row.content[:40],
                        _one_button(action, row.id, settings=settings, binding=_binding(message)),
                    )
                ]
                for row in matches[: settings.member_max_button_items]
            ]
        ),
    )


def _one_button(
    action: str, entity_id: uuid.UUID, *, settings: Settings, binding: MemberBinding
) -> str:
    from meobot.domain.member.callbacks import build

    return build(
        action,
        secret=settings.callback_secret,
        binding=binding,
        expires_at=utcnow() + timedelta(seconds=settings.member_flow_ttl_seconds),
        entity_id=entity_id,
    )


def _subject_of(text: str) -> str:
    """The reminder name inside "tạm dừng lịch nhắc đi dạy"."""
    from meobot.domain.member.normalization import strip_accents

    folded = strip_accents(text)
    for marker in ("lich nhac", "lich"):
        if marker in folded:
            return folded.split(marker, 1)[1].strip()
    return ""


async def _apply_to(
    message: Message,
    actor: Actor,
    database: Database,
    settings: Settings,
    action: str,
    reminder_id: uuid.UUID,
) -> None:
    text = await _run_management(actor, database, settings, action, reminder_id)
    await formatting.answer(message, formatting.escape(text))


async def _apply_management(
    query: CallbackQuery,
    actor: Actor,
    database: Database,
    settings: Settings,
    action: str,
    reminder_id: uuid.UUID | None,
) -> None:
    if reminder_id is None:
        await formatting.edit_callback(query, formatting.escape(NO_REMINDERS))
        return
    text = await _run_management(actor, database, settings, action, reminder_id)
    await formatting.edit_callback(query, formatting.escape(text))


async def _run_management(
    actor: Actor,
    database: Database,
    settings: Settings,
    action: str,
    reminder_id: uuid.UUID,
) -> str:
    """Apply one management action and return what to say about it.

    Every sentence returned here is past tense only because the transaction
    that produced it has committed by the time it is sent.
    """
    async with database.transaction() as session:
        service = ReminderService(session, settings)
        reminder = await session.get(Reminder, reminder_id)
        if reminder is None:
            return NO_REMINDERS
        try:
            if action == "rem.pause":
                await service.pause(actor=actor, reminder=reminder)
                return PAUSED
            if action == "rem.resume":
                await service.resume(actor=actor, reminder=reminder)
                return RESUMED
            await service.cancel(actor=actor, reminder=reminder)
            return STOPPED
        except MeoBotError as exc:
            return exc.message


@router.message(InMemberFlow(str(ReminderFlow.confirming.state)))
async def handle_amended_draft(
    message: Message,
    actor: Actor,
    database: Database,
    settings: Settings,
    state: FSMContext,
) -> None:
    """A new sentence while a draft is pending usually replaces it.

    Somebody who types a correction rather than pressing "Sửa lại" means the
    correction. Treating it as ordinary chat would answer the wrong question
    and leave the draft stranded.

    But not *every* sentence is a correction. "Lịch nhắc của tôi" while a draft
    is open is a request to see the list, not an attempt to reword the draft
    into one - and answering it with "MeoBot chưa rõ bạn muốn được nhắc lúc
    mấy giờ" is the kind of reply that makes a bot feel stuck. So a recognised
    management intent is honoured, and the draft is abandoned rather than
    silently retained.
    """
    text = (message.text or "").strip()
    if not text:
        return

    if _is_cancellation(text, settings):
        await state.clear()
        await formatting.answer(message, formatting.escape(CANCELLED_DRAFT))
        return

    match = classify(text, normalization_enabled=settings.member_vietnamese_normalization_enabled)
    if match.intent in HANDLED and match.intent is not MemberIntent.CREATE_REMINDER:
        await state.clear()
        await handle_reminder_intent(
            message,
            actor,
            database,
            settings,
            state,
            member_intent=match.intent,
            member_text=text,
        )
        return

    existing = await _load_draft(state, settings)
    if existing is None:
        # Nothing usable held any more - an expired or corrupt draft. Start
        # over rather than merging into something half-understood.
        await state.clear()
    await _start_creation(message, actor, database, settings, state, text, existing=existing)


def _is_cancellation(text: str, settings: Settings) -> bool:
    """Whether this reply means "stop", in the words people actually use."""
    folded = strip_accents(text).strip(" .!?")
    return folded in _CANCEL_WORDS


async def _load_draft(state: FSMContext, settings: Settings) -> ReminderDraftState | None:
    """The held draft, or ``None`` when there is none worth keeping."""
    stored = (await state.get_data()).get(DRAFT_KEY)
    if not isinstance(stored, dict):
        return None
    draft = ReminderDraftState.from_dict(stored)
    if draft is None or draft.is_expired(settings.member_flow_ttl_seconds):
        return None
    return draft


async def resume_parked_reminder(
    message: Message,
    actor: Actor,
    database: Database,
    settings: Settings,
    state: FSMContext,
) -> None:
    """Pick up a reminder that was waiting for the account to be completed.

    Called by ``/start``. Kept here rather than in the command handler so the
    draft's shape stays owned by one module, and so the command handler does
    not have to know what a reminder draft is.

    Three outcomes, and the middle one is the point of the hotfix:

    * nothing parked - say nothing, ``/start`` was just ``/start``;
    * a usable draft - show its preview, so the request the person already made
      simply continues;
    * a draft that cannot be resumed - **clear it** and say so. Leaving a
      broken flow active is how the reported bug turned one confusing message
      into five.
    """
    held = await _load_draft(state, settings)
    if held is None or not held.awaiting_onboarding:
        if held is None:
            # Nothing usable. Clear any residue so the next ordinary message is
            # not captured by a flow the person has forgotten about.
            current = await state.get_state()
            if current in {ReminderFlow.confirming.state, ReminderFlow.choosing_hour.state}:
                await state.clear()
                await formatting.answer(message, formatting.escape(RESUME_FAILED))
        return

    if actor.user_id is None or not held.is_complete:
        await state.clear()
        await formatting.answer(message, formatting.escape(RESUME_FAILED))
        return

    await formatting.answer(message, formatting.escape(RESUMED_AFTER_START))
    await _advance(message, actor, database, settings, state, held.cleared_of_onboarding())
