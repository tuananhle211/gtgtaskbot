"""Filing leave and late arrivals, and deciding them.

**One question at a time.** The flow asks for exactly the one thing it is
missing - :meth:`~meobot.domain.hr.models.HrRequestDraft.missing_field` returns
a single field name, so a handler cannot accidentally ask for four. Anything the
first message already supplied is never asked about again.

**State survives a restart** because it lives in aiogram's FSM, which this
deployment backs with PostgreSQL (:class:`~meobot.bot.storage.PostgresStorage`).
An expired flow fails safely: MeoBot says the session timed out and how to start
again, rather than silently attaching an answer to a request nobody remembers.

**Nothing is created until the preview is confirmed.** The draft is a value
object; the row appears on the confirm press and not before.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from meobot.application.audit_service import AuditService
from meobot.application.hr_notifications import HrNotificationService
from meobot.application.hr_request_service import HrRequestService
from meobot.application.member_interaction_service import ButtonSpec, MemberReply
from meobot.application.work_schedule_service import NOT_CONFIGURED, WorkScheduleService
from meobot.bot import formatting, member_keyboards
from meobot.bot.addressing import bot_username_of, strip_bot_mention
from meobot.bot.handlers.member import send
from meobot.bot.member_filters import InMemberFlow, MemberIntentFilter
from meobot.core.config import Settings
from meobot.core.errors import MeoBotError
from meobot.core.logging import get_logger
from meobot.core.time import utcnow
from meobot.db.models.user import User
from meobot.db.session import Database
from meobot.domain.hr.models import (
    HrRequestDraft,
    HrRequestType,
    status_label,
    type_label,
)
from meobot.domain.hr.schedule import (
    DEFAULT_SCHEDULE,
    WorkSchedule,
    describe_period,
    format_local_date,
    format_local_time,
    leave_interval,
    resolve_clock,
    resolve_date,
    resolve_late_minutes,
    resolve_minutes,
    resolve_period,
)
from meobot.domain.identity.labels import role_label
from meobot.domain.identity.models import Actor, Role
from meobot.domain.member import copy
from meobot.domain.member.callbacks import data_pattern, parse
from meobot.domain.member.intents import MemberIntent
from meobot.domain.member.normalization import normalize

logger = get_logger(__name__)

router = Router(name="hr")


class HrFlow(StatesGroup):
    """The guided request conversation.

    Deliberately few states: each one is a single missing fact, which is what
    keeps the one-question rule structural rather than a habit.
    """

    choosing_period = State()
    entering_arrival = State()
    entering_reason = State()
    confirming = State()


#: The buttons this router owns, named up front so the callback filter can be
#: built before the handler table below exists.
HR_BUTTON_ACTIONS = (
    "hr.period.morning",
    "hr.period.afternoon",
    "hr.period.full",
    "hr.period.hours",
    "flow.confirm",
    "flow.edit",
    "flow.discard",
    "hr.approve",
    "hr.reject",
    "hr.withdraw",
)
HR_BUTTON_PATTERN = data_pattern(HR_BUTTON_ACTIONS)

ASK_PERIOD = "Bạn muốn nghỉ khoảng thời gian nào?"
ASK_ARRIVAL = "Ngày mai bạn dự kiến có mặt lúc mấy giờ?"
ASK_ARRIVAL_TODAY = "Bạn dự kiến có mặt lúc mấy giờ?"
ASK_REASON = "Bạn cho mình biết lý do nhé. Nếu không cần thì bấm bỏ qua."
SENT = "✅ Mình đã gửi yêu cầu tới {approver}. Bạn sẽ nhận được thông báo khi có kết quả."
WITHDRAWN = "↩️ Mình đã rút yêu cầu này giúp bạn."


# --- Entry points -----------------------------------------------------------
#: The states this router owns. Scoped by name so an ``/add_sheet`` in progress
#: is never captured here.
HR_STATES: tuple[str, ...] = tuple(
    str(state.state)
    for state in (
        HrFlow.choosing_period,
        HrFlow.entering_arrival,
        HrFlow.entering_reason,
        HrFlow.confirming,
    )
)

#: The two things somebody says to *start* filing, plus withdrawing.
ENTRY_INTENTS = frozenset(
    {
        MemberIntent.REQUEST_LEAVE,
        MemberIntent.REQUEST_LATE,
        MemberIntent.CANCEL_HR_REQUEST,
    }
)


@router.message(InMemberFlow(*HR_STATES))
async def handle_hr_flow_reply(
    message: Message,
    actor: Actor,
    settings: Settings,
    database: Database,
    state: FSMContext,
    request_id: uuid.UUID,
) -> None:
    """Answer the one question the flow last asked."""
    await _continue_flow(
        message,
        actor=actor,
        settings=settings,
        database=database,
        state=state,
        request_id=request_id,
    )


@router.message(MemberIntentFilter(ENTRY_INTENTS))
async def handle_hr_text(
    message: Message,
    actor: Actor,
    settings: Settings,
    database: Database,
    state: FSMContext,
    request_id: uuid.UUID,
    member_intent: MemberIntent,
    member_text: str,
) -> None:
    """Start a leave or late request, or withdraw the last one."""
    if member_intent is MemberIntent.REQUEST_LEAVE:
        await _begin_leave(
            message,
            actor=actor,
            settings=settings,
            database=database,
            state=state,
            text=member_text,
        )
    elif member_intent is MemberIntent.REQUEST_LATE:
        await _begin_late(
            message,
            actor=actor,
            settings=settings,
            database=database,
            state=state,
            text=member_text,
        )
    else:
        await _withdraw_latest(
            message, actor=actor, settings=settings, database=database, request_id=request_id
        )


async def _schedule_for(database: Database) -> tuple[WorkSchedule, bool]:
    """``(schedule, is_configured)``.

    The flag matters: with no configured schedule MeoBot must not compute
    lateness, so it asks for an arrival time instead of assuming one.
    """
    async with database.session() as session:
        configured = await WorkScheduleService(session).active()
    return configured or DEFAULT_SCHEDULE, configured is not None


async def _begin_leave(
    message: Message,
    *,
    actor: Actor,
    settings: Settings,
    database: Database,
    state: FSMContext,
    text: str,
) -> None:
    """Read whatever the first message already said, then ask for one thing."""
    schedule, _ = await _schedule_for(database)
    matchable = normalize(text, enabled=settings.member_vietnamese_normalization_enabled).matchable
    day = resolve_date(matchable, now=utcnow(), schedule=schedule)
    period = resolve_period(matchable)

    draft = HrRequestDraft(
        request_type=period,
        work_date=day,
        reason=_reason_from(text),
    )
    await _store(state, draft)

    if draft.work_date is None:
        await state.set_state(HrFlow.choosing_period)
        await formatting.answer(
            message, formatting.escape("Bạn muốn nghỉ ngày nào? Ví dụ: ngày mai, thứ sáu.")
        )
        return
    if draft.request_type is None:
        await state.set_state(HrFlow.choosing_period)
        await _ask_period(message, settings=settings)
        return
    await _preview(message, actor=actor, settings=settings, database=database, state=state)


async def _begin_late(
    message: Message,
    *,
    actor: Actor,
    settings: Settings,
    database: Database,
    state: FSMContext,
    text: str,
) -> None:
    """Work out the arrival time and the lateness, or ask for what is missing."""
    schedule, configured = await _schedule_for(database)
    matchable = normalize(text, enabled=settings.member_vietnamese_normalization_enabled).matchable
    day = (
        resolve_date(matchable, now=utcnow(), schedule=schedule)
        or datetime.now(tz=schedule.zone).date()
    )

    arrival, minutes = resolve_late_minutes(
        arrival=resolve_clock(matchable),
        minutes=resolve_minutes(matchable),
        schedule=schedule if configured else None,
    )

    draft = HrRequestDraft(
        request_type=HrRequestType.LATE_ARRIVAL,
        work_date=day,
        expected_arrival_at=schedule.local(day, arrival).astimezone(UTC) if arrival else None,
        late_minutes=minutes,
        reason=_reason_from(text),
    )
    await _store(state, draft)

    if arrival is None:
        # Either nothing was said, or nothing *could* be worked out because the
        # office hours are unknown. Ask rather than invent a start time.
        await state.set_state(HrFlow.entering_arrival)
        prefix = "" if configured else NOT_CONFIGURED + "\n\n"
        await formatting.answer(message, formatting.escape(prefix + ASK_ARRIVAL_TODAY))
        return
    await _preview(message, actor=actor, settings=settings, database=database, state=state)


def _reason_from(text: str) -> str:
    """Pull a stated reason out of the first message, if there is one."""
    lowered = normalize(text).matchable
    for marker in (" vi ", " do ", " boi vi ", " co viec "):
        if marker in lowered:
            index = lowered.index(marker)
            return text[index:].strip(" ,.")[:500]
    return ""


# --- Continuing the conversation --------------------------------------------
async def _continue_flow(
    message: Message,
    *,
    actor: Actor,
    settings: Settings,
    database: Database,
    state: FSMContext,
    request_id: uuid.UUID,
) -> None:
    """Answer the one question that was asked, then ask the next or preview."""
    draft = await _load(state)
    if draft is None:
        await state.clear()
        await formatting.answer(message, formatting.escape(copy.FLOW_EXPIRED))
        return

    schedule, configured = await _schedule_for(database)
    text = strip_bot_mention(message.text or "", bot_username_of(message)).strip()
    matchable = normalize(text, enabled=settings.member_vietnamese_normalization_enabled).matchable
    current = await state.get_state()

    if current == HrFlow.choosing_period.state:
        day = draft.work_date or resolve_date(matchable, now=utcnow(), schedule=schedule)
        period = draft.request_type or resolve_period(matchable)
        draft = draft.model_copy(update={"work_date": day, "request_type": period})
        await _store(state, draft)
        if draft.work_date is None:
            await formatting.answer(
                message, formatting.escape("Bạn muốn nghỉ ngày nào? Ví dụ: ngày mai, thứ sáu.")
            )
            return
        if draft.request_type is None:
            await _ask_period(message, settings=settings)
            return
        await _preview(message, actor=actor, settings=settings, database=database, state=state)
        return

    if current == HrFlow.entering_arrival.state:
        arrival, minutes = resolve_late_minutes(
            arrival=resolve_clock(matchable),
            minutes=resolve_minutes(matchable),
            schedule=schedule if configured else None,
        )
        if arrival is None:
            await formatting.answer(message, formatting.escape(ASK_ARRIVAL_TODAY))
            return
        day = draft.work_date or datetime.now(tz=schedule.zone).date()
        draft = draft.model_copy(
            update={
                "expected_arrival_at": schedule.local(day, arrival).astimezone(UTC),
                "late_minutes": minutes,
                "work_date": day,
            }
        )
        await _store(state, draft)
        await _preview(message, actor=actor, settings=settings, database=database, state=state)
        return

    if current == HrFlow.entering_reason.state:
        draft = draft.model_copy(update={"reason": text[:500]})
        await _store(state, draft)
        await _preview(message, actor=actor, settings=settings, database=database, state=state)
        return


async def _ask_period(message: Message, *, settings: Settings) -> None:
    """The four-button period question."""
    if message.bot is None or message.from_user is None:  # pragma: no cover
        return
    binding = member_keyboards.binding_for(
        bot_id=message.bot.id,
        telegram_user_id=message.from_user.id,
        chat_id=message.chat.id,
    )
    rows = [
        [
            ButtonSpec(copy.Button.MORNING.value, "hr.period.morning"),
            ButtonSpec(copy.Button.AFTERNOON.value, "hr.period.afternoon"),
        ],
        [
            ButtonSpec(copy.Button.FULL_DAY.value, "hr.period.full"),
            ButtonSpec(copy.Button.PICK_HOURS.value, "hr.period.hours"),
        ],
    ]
    await formatting.answer(
        message,
        formatting.escape(ASK_PERIOD),
        reply_markup=member_keyboards.render(rows, settings=settings, binding=binding),
    )


# --- Preview and confirmation ------------------------------------------------
async def _preview(
    message: Message,
    *,
    actor: Actor,
    settings: Settings,
    database: Database,
    state: FSMContext,
) -> None:
    """Show exactly what will be filed, and wait for a press."""
    draft = await _load(state)
    if draft is None or draft.request_type is None or draft.work_date is None:
        await state.clear()
        await formatting.answer(message, formatting.escape(copy.FLOW_EXPIRED))
        return

    schedule, _ = await _schedule_for(database)
    await state.set_state(HrFlow.confirming)

    if draft.request_type is HrRequestType.LATE_ARRIVAL:
        arrival = (
            format_local_time(draft.expected_arrival_at, schedule)
            if draft.expected_arrival_at
            else "chưa rõ"
        )
        lines = [
            "Bạn đang tạo yêu cầu đi muộn:",
            "",
            f"• Ngày: {format_local_date(draft.work_date)}",
            f"• Giờ làm việc bắt đầu: {schedule.day_start.strftime('%H:%M')}",
            f"• Giờ dự kiến có mặt: {arrival}",
        ]
        if draft.late_minutes is not None:
            lines.append(f"• Đi muộn dự kiến: {draft.late_minutes} phút")
    else:
        lines = [
            "Bạn đang tạo yêu cầu nghỉ phép:",
            "",
            f"• Thời gian: {describe_period(draft.request_type, day=draft.work_date)}",
        ]
    lines.append(f"• Lý do: {draft.reason or 'Không có lý do cụ thể'}")
    lines.append(f"• Người duyệt: {role_label(Role.OWNER)}")

    if message.bot is None or message.from_user is None:  # pragma: no cover
        return
    binding = member_keyboards.binding_for(
        bot_id=message.bot.id,
        telegram_user_id=message.from_user.id,
        chat_id=message.chat.id,
    )
    rows = [
        [ButtonSpec(copy.Button.SEND_REQUEST.value, "flow.confirm")],
        [
            ButtonSpec(copy.Button.EDIT_AGAIN.value, "flow.edit"),
            ButtonSpec(copy.Button.DISCARD.value, "flow.discard"),
        ],
    ]
    await formatting.answer(
        message,
        formatting.escape("\n".join(lines)),
        reply_markup=member_keyboards.render(rows, settings=settings, binding=binding),
    )


@router.callback_query(F.data.regexp(HR_BUTTON_PATTERN))
async def handle_hr_button(
    query: CallbackQuery,
    actor: Actor,
    settings: Settings,
    database: Database,
    state: FSMContext,
    request_id: uuid.UUID,
) -> None:
    """Every HR button: period choice, confirm, discard, approve, reject."""
    if query.message is None or query.bot is None or query.data is None:  # pragma: no cover
        return
    binding = member_keyboards.binding_for(
        bot_id=query.bot.id,
        telegram_user_id=query.from_user.id,
        chat_id=query.message.chat.id,
    )
    payload = parse(query.data, secret=settings.callback_secret, binding=binding)
    await query.answer()
    if payload is None:
        # Signed for a different person, chat or bot - or not signed at all.
        await query.answer(copy.Problem.BUTTON_NOT_YOURS.value, show_alert=True)
        return

    if payload.is_expired(utcnow()):
        await formatting.edit_callback(query, formatting.escape(copy.Problem.BUTTON_EXPIRED.value))
        return

    handler = _HR_ACTIONS[payload.action]
    await handler(
        query,
        actor=actor,
        settings=settings,
        database=database,
        state=state,
        request_id=request_id,
        entity_id=payload.entity_id,
        version=payload.version,
    )


async def _pick_period(
    query: CallbackQuery,
    *,
    settings: Settings,
    database: Database,
    state: FSMContext,
    actor: Actor,
    request_id: uuid.UUID,
    entity_id: uuid.UUID | None,
    version: int,
    period: HrRequestType,
) -> None:
    draft = await _load(state)
    if draft is None or not isinstance(query.message, Message):
        await state.clear()
        await formatting.answer_callback(query, formatting.escape(copy.FLOW_EXPIRED))
        return
    await _store(state, draft.model_copy(update={"request_type": period}))
    await _preview(query.message, actor=actor, settings=settings, database=database, state=state)


async def _confirm(
    query: CallbackQuery,
    *,
    settings: Settings,
    database: Database,
    state: FSMContext,
    actor: Actor,
    request_id: uuid.UUID,
    entity_id: uuid.UUID | None,
    version: int,
) -> None:
    """Create the request. The only place a row appears."""
    draft = await _load(state)
    if draft is None or draft.request_type is None or draft.work_date is None:
        await state.clear()
        await formatting.edit_callback(query, formatting.escape(copy.FLOW_EXPIRED))
        return

    schedule, _ = await _schedule_for(database)
    # Clearing first makes a double press harmless: the second one finds no
    # draft and reports the flow as finished rather than filing twice.
    await state.clear()

    try:
        async with database.transaction() as session:
            service = HrRequestService(session, AuditService(session))
            local_start, local_end = leave_interval(
                draft.request_type, day=draft.work_date, schedule=schedule
            )
            row = await service.submit(
                actor=actor,
                request_id=request_id,
                request_type=draft.request_type,
                work_date=draft.work_date,
                schedule=schedule,
                start_at=local_start.astimezone(UTC),
                end_at=local_end.astimezone(UTC),
                expected_arrival_at=draft.expected_arrival_at,
                late_minutes=draft.late_minutes,
                reason=draft.reason,
                source="button",
            )
            # The approval card is queued *here*, in the same transaction as
            # the request itself. A Telegram outage can no longer produce a
            # saved request nobody was told about - nor the reverse.
            requester = await session.get(User, row.requester_user_id)
            if requester is not None:
                await HrNotificationService(session, settings).on_submitted(
                    row=row,
                    requester=requester,
                    schedule=schedule,
                    source_chat_id=query.message.chat.id
                    if isinstance(query.message, Message)
                    else 0,
                )
    except MeoBotError as exc:
        await formatting.edit_callback(query, formatting.escape(exc.message))
        return

    await formatting.edit_callback(
        query, formatting.escape(SENT.format(approver=role_label(Role.OWNER)))
    )


async def _edit(
    query: CallbackQuery,
    *,
    settings: Settings,
    database: Database,
    state: FSMContext,
    actor: Actor,
    request_id: uuid.UUID,
    entity_id: uuid.UUID | None,
    version: int,
) -> None:
    """Go back to the period question, keeping everything already answered."""
    if not isinstance(query.message, Message):  # pragma: no cover
        return
    await state.set_state(HrFlow.choosing_period)
    await _ask_period(query.message, settings=settings)


async def _discard(
    query: CallbackQuery,
    *,
    settings: Settings,
    database: Database,
    state: FSMContext,
    actor: Actor,
    request_id: uuid.UUID,
    entity_id: uuid.UUID | None,
    version: int,
) -> None:
    await state.clear()
    await formatting.edit_callback(query, formatting.escape(copy.CANCELLED))


async def _decide(
    query: CallbackQuery,
    *,
    settings: Settings,
    database: Database,
    state: FSMContext,
    actor: Actor,
    request_id: uuid.UUID,
    entity_id: uuid.UUID | None,
    version: int,
    approve: bool,
) -> None:
    """Approve or refuse. Idempotent, version-checked, audited."""
    if entity_id is None:
        await formatting.edit_callback(
            query, formatting.escape(copy.Problem.REQUEST_NOT_FOUND.value)
        )
        return
    try:
        async with database.transaction() as session:
            service = HrRequestService(session, AuditService(session))
            row = await (service.approve if approve else service.reject)(
                actor=actor,
                request_id=request_id,
                hr_request_id=entity_id,
                expected_version=version or None,
            )
            summary = (
                f"{type_label(row.request_type)} — "
                f"{describe_period(row.request_type, day=row.work_date, end_day=row.end_date)} — "
                f"{status_label(row.status)}"
            )
            # The Member's private result and the neutral group update are
            # queued in the same transaction as the decision. A failure to
            # deliver either can never undo the approval.
            schedule, _ = await _schedule_for(database)
            await HrNotificationService(session, settings).on_decided(
                row=row,
                schedule=schedule,
                bot_identity=query.bot.id if query.bot is not None else 0,
                approved=approve,
                source_chat_id=query.message.chat.id if isinstance(query.message, Message) else 0,
            )
    except MeoBotError as exc:
        await formatting.edit_callback(query, formatting.escape(exc.message))
        return
    await formatting.edit_callback(query, formatting.escape(summary))


async def _withdraw_button(
    query: CallbackQuery,
    *,
    settings: Settings,
    database: Database,
    state: FSMContext,
    actor: Actor,
    request_id: uuid.UUID,
    entity_id: uuid.UUID | None,
    version: int,
) -> None:
    if entity_id is None:
        await formatting.edit_callback(
            query, formatting.escape(copy.Problem.REQUEST_NOT_FOUND.value)
        )
        return
    try:
        async with database.transaction() as session:
            service = HrRequestService(session, AuditService(session))
            await service.withdraw(actor=actor, request_id=request_id, hr_request_id=entity_id)
    except MeoBotError as exc:
        await formatting.edit_callback(query, formatting.escape(exc.message))
        return
    await formatting.edit_callback(query, formatting.escape(WITHDRAWN))


async def _withdraw_latest(
    message: Message,
    *,
    actor: Actor,
    settings: Settings,
    database: Database,
    request_id: uuid.UUID,
) -> None:
    """ "Huỷ đơn vừa gửi" - only when exactly one open request could be meant."""
    if actor.user_id is None:
        return
    async with database.transaction() as session:
        service = HrRequestService(session, AuditService(session))
        target = await service.latest_open_for(user_id=actor.user_id)
        if target is None:
            reply = MemberReply(
                text=copy.ASK_WHICH_WORK.replace("việc", "yêu cầu"),
                buttons=[[ButtonSpec(copy.Button.MY_REQUESTS.value, "hr.mine")]],
            )
            await send(message, reply, settings=settings, database=database, actor=actor)
            return
        try:
            await service.withdraw(actor=actor, request_id=request_id, hr_request_id=target.id)
        except MeoBotError as exc:
            await formatting.answer(message, formatting.escape(exc.message))
            return
    await formatting.answer(message, formatting.escape(WITHDRAWN))


# --- Flow storage -----------------------------------------------------------
async def _store(state: FSMContext, draft: HrRequestDraft) -> None:
    """Persist the draft. Dates go in as ISO strings so JSON can hold them."""
    await state.update_data(hr_draft=draft.model_dump(mode="json"))


async def _load(state: FSMContext) -> HrRequestDraft | None:
    """Restore the draft, or ``None`` when the flow is gone."""
    data = await state.get_data()
    raw = data.get("hr_draft")
    if not raw:
        return None
    try:
        return HrRequestDraft.model_validate(raw)
    except Exception:  # pragma: no cover - corrupt state fails safe
        return None


def _period_handler(period: HrRequestType):  # type: ignore[no-untyped-def]
    async def handler(query: CallbackQuery, **kwargs: object) -> None:
        await _pick_period(query, period=period, **kwargs)  # type: ignore[arg-type]

    return handler


def _decision_handler(approve: bool):  # type: ignore[no-untyped-def]
    async def handler(query: CallbackQuery, **kwargs: object) -> None:
        await _decide(query, approve=approve, **kwargs)  # type: ignore[arg-type]

    return handler


#: The buttons this router owns. Anything else falls through to the Member one.
_HR_ACTIONS = {
    "hr.period.morning": _period_handler(HrRequestType.MORNING_LEAVE),
    "hr.period.afternoon": _period_handler(HrRequestType.AFTERNOON_LEAVE),
    "hr.period.full": _period_handler(HrRequestType.FULL_DAY_LEAVE),
    "hr.period.hours": _period_handler(HrRequestType.HOURLY_LEAVE),
    "flow.confirm": _confirm,
    "flow.edit": _edit,
    "flow.discard": _discard,
    "hr.approve": _decision_handler(True),
    "hr.reject": _decision_handler(False),
    "hr.withdraw": _withdraw_button,
}
