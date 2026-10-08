"""Sending one announcement to several registered groups, in Vietnamese.

**This router runs before free conversation, and before the single-destination
announcement router.** That ordering is the fix for the two reported defects,
and it is not a detail:

* the owner was shown three candidate groups, replied **"Tất cả"**, and the
  message was classified afresh - as conversation - so MeoBot asked what action
  they wanted to perform on those groups;
* the owner was shown a preview, replied **"Xác nhận"**, and the same thing
  happened, so MeoBot asked which recipients they meant.

:class:`InDispatchDraft` is what stops that. While a draft is open it owns every
text turn from the person who opened it, in the chat they opened it in, and the
words are read by
:func:`~meobot.domain.dispatch.phrases.read_continuation` rather than by a
model. "Tất cả", "hai group đầu", "bỏ Test" and "Ok gửi nhé" therefore cannot
reach the LLM at all - not because a prompt tells it to stay away, but because
the update never gets that far.

**No handler here sends a cross-chat message.** Everything destined for a group
goes through :class:`~meobot.application.dispatch_service.DispatchService` into
the outbox and out through ``q_notifications``. What these handlers write is the
chat the person is typing in: a list, a picker, a preview, a receipt.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from aiogram import F, Router
from aiogram.filters import Command, Filter
from aiogram.types import CallbackQuery, Message
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.chat_registry_service import ChatRegistryService
from meobot.application.dispatch_draft_service import DispatchDraftService
from meobot.application.dispatch_service import DispatchService
from meobot.application.member_interaction_service import ButtonSpec
from meobot.application.recipient_resolver import (
    AudienceResolution,
    RecipientCandidate,
    RecipientResolver,
)
from meobot.bot import dispatch_cards, formatting, member_keyboards
from meobot.bot.addressing import bot_username_of, is_addressed_to_bot, strip_bot_mention
from meobot.bot.member_filters import MemberIntentFilter
from meobot.core.config import Settings
from meobot.core.errors import MeoBotError
from meobot.core.logging import get_logger
from meobot.core.time import utcnow
from meobot.db.models.dispatch import MessageDispatchDraft, MessageDispatchDraftRecipient
from meobot.db.models.notifications import TelegramChat
from meobot.db.session import Database
from meobot.domain.dispatch.models import DraftStatus, SelectionSource
from meobot.domain.dispatch.phrases import (
    AudienceScope,
    Continuation,
    ContinuationKind,
    read_continuation,
)
from meobot.domain.dispatch.splitting import split_announcement
from meobot.domain.identity.labels import role_label
from meobot.domain.identity.models import Actor
from meobot.domain.member import copy
from meobot.domain.member.callbacks import data_pattern, parse
from meobot.domain.member.intents import MemberIntent
from meobot.domain.notifications.naming import extract_send_content

logger = get_logger(__name__)

router = Router(name="dispatch")

NO_PERMISSION = (
    "Bạn chưa được phép gửi thông báo tới group.\n"
    "Bạn nhờ Trưởng phòng gán quyền quản lý group giúp bạn nhé."
)

ASK_FOR_CONTENT = "Bạn viết giúp mình nội dung thông báo nhé."

REWRITE_HELP = (
    "Bạn nhắn lại nội dung thông báo và nơi nhận giúp mình nhé.\n"
    "Ví dụ: “Gửi thông báo này vào các group Nội dung: <nội dung>.”"
)

#: What the sender reads when the draft could not be written down.
#:
#: The middle line is the load-bearing one. A person who has just typed out a
#: department announcement and got an error has exactly one question - *did any
#: of it go out?* - and the honest answer is no: the draft is written before
#: anything is queued, so a failure here means no outbox row exists and no group
#: received anything. Saying so is the difference between a person retrying
#: calmly and a person sending it again "just in case".
DRAFT_STORAGE_FAILED = (
    "TasksBot chưa tạo được bản xem trước cho thông báo này.\n\n"
    "Chưa có nội dung nào được gửi.\n"
    "Bạn thử lại sau khi hệ thống được cập nhật nhé."
)

#: The same guarantee, told at the moment of confirmation. The preview did
#: exist by then, so claiming otherwise would be wrong - but the part that
#: matters is identical: the confirming transaction is all-or-nothing, so a
#: failure means no dispatch, no recipient row and no outbox intent.
CONFIRM_STORAGE_FAILED = (
    "TasksBot chưa ghi nhận được lần gửi này.\n\n"
    "Chưa có nội dung nào được gửi.\n"
    "Bạn thử lại sau khi hệ thống được cập nhật nhé."
)

NOT_FOUND = (
    "TasksBot chưa tìm thấy group nào phù hợp trong danh sách đã đăng ký.\n"
    "Bạn vào chính group đó và nhắn “đăng ký group này” để TasksBot ghi nhận nhé."
)

AMBIGUOUS = (
    "Có nhiều group cùng tên nên TasksBot chưa chắc bạn muốn gửi vào đâu.\nBạn chọn giúp mình nhé."
)

#: Every button this router draws. Matched as a group so aiogram hands it only
#: its own presses - returning early from a handler whose filters matched would
#: consume the update, not pass it on.
DISPATCH_ACTIONS = (
    "disp.select",
    "disp.deselect",
    "disp.all",
    "disp.none",
    "disp.done",
    "disp.send",
    "disp.cancel",
    "disp.reselect",
    "disp.detail",
    "disp.retry",
    "disp.summary",
    "disp.page",
    "disp.list.all",
    "disp.list.each",
    "disp.list.healthy",
    "disp.edit",
)


class InDispatchDraft(Filter):
    """Matches while this person has an open draft in this chat.

    The check is a durable one - a row in ``message_dispatch_drafts`` - not FSM
    state, so it survives a restart. A bot deploy in the middle of somebody
    composing a department announcement must not turn their next "Xác nhận"
    into conversation.

    Bound to bot, chat *and* person: a draft started privately is not
    continuable from a group, and one person's "Xác nhận" can never confirm
    somebody else's draft.
    """

    async def __call__(
        self,
        message: Message,
        settings: Settings | None = None,
        database: Database | None = None,
        **_: object,
    ) -> bool:
        if settings is None or database is None or not message.text or message.from_user is None:
            return False
        if message.bot is None:  # pragma: no cover - always set by aiogram
            return False
        if not is_addressed_to_bot(message, require_mention=settings.chat_group_requires_mention):
            return False
        async with database.transaction() as session:
            draft = await DispatchDraftService(session, settings).active(
                bot_identity=message.bot.id,
                source_chat_id=message.chat.id,
                telegram_user_id=message.from_user.id,
            )
            return draft is not None


# --- Continuing an open draft -----------------------------------------------
@router.message(Command("cancel_flow"), InDispatchDraft())
async def handle_cancel_command(message: Message, settings: Settings, database: Database) -> None:
    """``/cancel_flow`` while composing an announcement abandons it.

    Handled here rather than left to the generic flow-cancel because this draft
    lives in a table, not in FSM state, and clearing the FSM would leave the row
    open - so the next "Xác nhận" would still find something to confirm.
    """
    if message.bot is None or message.from_user is None:  # pragma: no cover
        return
    async with database.transaction() as session:
        drafts = DispatchDraftService(session, settings)
        draft = await drafts.active(
            bot_identity=message.bot.id,
            source_chat_id=message.chat.id,
            telegram_user_id=message.from_user.id,
        )
        if draft is not None:
            await drafts.cancel(draft)
    await formatting.answer(message, formatting.escape(copy.CANCELLED))


@router.message(InDispatchDraft())
async def handle_draft_reply(
    message: Message,
    actor: Actor,
    settings: Settings,
    database: Database,
    request_id: uuid.UUID,
) -> None:
    """Read a reply to an open draft, and never hand it to a model.

    Every branch here answers with the draft's own card. Even an unrecognised
    message re-shows what is on screen rather than falling through: while
    somebody is choosing who reads an announcement, "Mình chưa thể gửi tin
    trong lượt này" is not an answer to anything they asked.
    """
    if message.bot is None or message.from_user is None or not message.text:  # pragma: no cover
        return
    text = strip_bot_mention(message.text, bot_username_of(message)).strip()

    try:
        async with database.transaction() as session:
            drafts = DispatchDraftService(session, settings)
            draft = await drafts.active(
                bot_identity=message.bot.id,
                source_chat_id=message.chat.id,
                telegram_user_id=message.from_user.id,
            )
            if draft is None:
                await formatting.answer(message, formatting.escape(dispatch_cards.DRAFT_EXPIRED))
                return

            candidates = await drafts.recipients(draft.id)
            reading = read_continuation(text, candidate_count=len(candidates))
            card = await _apply_continuation(
                message=message,
                session=session,
                settings=settings,
                actor=actor,
                request_id=request_id,
                draft=draft,
                drafts=drafts,
                candidates=candidates,
                reading=reading,
            )
            if card is None:
                return
            version = draft.version
    except SQLAlchemyError:
        # A reply to an open draft is the turn most likely to be a "Xác nhận",
        # so silence here is the most expensive silence there is.
        await _report_storage_failure(
            message,
            CONFIRM_STORAGE_FAILED if _is_confirmation(text) else DRAFT_STORAGE_FAILED,
            request_id=request_id,
        )
        return

    await _draw(message, card, settings=settings, version=version)


def _is_confirmation(text: str) -> bool:
    """Whether this turn was somebody saying "gửi đi", for the failure wording."""
    return read_continuation(text).kind is ContinuationKind.CONFIRM


async def _report_storage_failure(message: Message, body: str, *, request_id: uuid.UUID) -> None:
    """Say that nothing was written, and nothing was sent.

    Logged at exception level with the correlation id, because a person being
    told "thử lại sau" is not a resolution - somebody has to be able to find
    the failure afterwards and fix it.
    """
    logger.exception("dispatch_persistence_failed", extra={"request_id": str(request_id)})
    await formatting.answer(message, formatting.escape(body))


async def _apply_continuation(
    *,
    message: Message,
    session: AsyncSession,
    settings: Settings,
    actor: Actor,
    request_id: uuid.UUID,
    draft: MessageDispatchDraft,
    drafts: DispatchDraftService,
    candidates: Sequence[MessageDispatchDraftRecipient],
    reading: Continuation,
) -> dispatch_cards.Card | None:
    """Turn one reading of a reply into the next card, or send the announcement.

    Returns ``None`` when the branch has already answered - confirming sends
    its own receipt, and cancelling says so.
    """
    kind, names, excluded, indices = (
        reading.kind,
        reading.names,
        reading.excluded,
        reading.indices,
    )
    pool = await _chats_of(session, candidates)

    if kind is ContinuationKind.CANCEL:
        await drafts.cancel(draft)
        await formatting.answer(message, formatting.escape(copy.CANCELLED))
        return None

    if kind is ContinuationKind.EDIT_CONTENT:
        await drafts.cancel(draft)
        await formatting.answer(message, formatting.escape(REWRITE_HELP))
        return None

    if kind is ContinuationKind.RESELECT:
        return await _reselect(session, settings, actor, draft, drafts, message=message)

    if kind is ContinuationKind.CONFIRM:
        return await _confirm(
            message=message,
            session=session,
            settings=settings,
            actor=actor,
            request_id=request_id,
            draft=draft,
            drafts=drafts,
        )

    if kind is ContinuationKind.SELECT_ALL:
        await drafts.select_all(draft)
        if excluded:
            removed, _ = RecipientResolver.match_names(excluded, pool)
            await drafts.exclude(draft, [row.id for row in removed])
        return await _preview_card(session, settings, actor, draft, drafts)

    if kind is ContinuationKind.SELECT_INDICES:
        await drafts.select_positions(draft, indices)
        return await _preview_card(session, settings, actor, draft, drafts)

    if kind is ContinuationKind.EXCLUDE_NAMES:
        removed, missing = RecipientResolver.match_names(excluded, pool)
        if not removed:
            return _unmatched_card(missing, candidates)
        await drafts.exclude(draft, [row.id for row in removed])
        return await _preview_card(session, settings, actor, draft, drafts)

    if kind is ContinuationKind.ONLY_NAMES:
        kept, missing = RecipientResolver.match_names(names, pool)
        if not kept:
            return _unmatched_card(missing, candidates)
        await drafts.restrict_to(draft, [row.id for row in kept])
        return await _preview_card(session, settings, actor, draft, drafts)

    if kind in {ContinuationKind.ADD_NAMES, ContinuationKind.SELECT_NAMES}:
        found, missing = RecipientResolver.match_names(names, pool)
        if not found:
            # Not on the card, so look at the whole registry - "thêm group
            # Test" is a request to widen the list, not a typo in it.
            registry = await ChatRegistryService(session).visible_to(
                actor=actor, bot_identity=draft.bot_identity
            )
            found, missing = RecipientResolver.match_names(names, registry)
            if not found:
                return _unmatched_card(missing, candidates)
            permitted = await DispatchService(
                session, settings, AuditService(session)
            ).permitted_ids(actor, found)
            await drafts.add(draft, found, permitted_ids=permitted)
            if kind is ContinuationKind.SELECT_NAMES:
                await drafts.restrict_to(draft, [row.id for row in found])
            return await _preview_card(session, settings, actor, draft, drafts)

        if kind is ContinuationKind.ADD_NAMES:
            existing = [row for row in candidates if row.selected]
            await drafts.restrict_to(
                draft,
                [row.recipient_chat_row_id for row in existing] + [row.id for row in found],
            )
        else:
            await drafts.restrict_to(draft, [row.id for row in found])
        return await _preview_card(session, settings, actor, draft, drafts)

    # Understood as nothing in particular. Re-show what is on screen; a draft
    # owns its turn, and the conversation model has no business here.
    refreshed = await drafts.recipients(draft.id)
    if draft.status is DraftStatus.PREVIEW:
        card = await _preview_card(session, settings, actor, draft, drafts)
        return dispatch_cards.Card(
            text=f"{formatting.escape(dispatch_cards.AWAITING_CONFIRM)}\n\n{card.text}",
            buttons=card.buttons,
        )
    return dispatch_cards.Card(
        text=formatting.escape(dispatch_cards.STILL_CHOOSING),
        buttons=dispatch_cards.selection_keyboard(refreshed),
    )


def _unmatched_card(
    missing: Sequence[str], candidates: Sequence[MessageDispatchDraftRecipient]
) -> dispatch_cards.Card:
    """Say which words could not be placed, and re-show the list.

    Never a nearest match. Posting an internal announcement into the group with
    the most similar name is the one outcome worth several extra taps to avoid.
    """
    quoted = ", ".join(f"“{name}”" for name in missing) or "tên group bạn vừa nhắn"
    lines = [
        formatting.escape(f"TasksBot chưa tìm thấy {quoted} trong danh sách group đang chọn."),
        "",
        formatting.escape("Bạn chọn giúp mình bằng các nút phía dưới nhé."),
    ]
    return dispatch_cards.Card(
        text="\n".join(lines), buttons=dispatch_cards.selection_keyboard(candidates)
    )


class NeedsSplitting(Filter):
    """Matches a single-destination announcement too long for one Telegram message.

    The single-destination path from 0.6.0a2 sends exactly one message, and
    capped its content at 3000 characters to stay under Telegram's limit - so a
    longer announcement lost its tail, silently. That is reproduction C, and it
    is not specific to sending somewhere *several* times.

    Rather than duplicate the splitter into the older handler, a long
    announcement is routed here whatever its destination count. A short one
    falls through untouched, so every existing single-destination behaviour -
    the preview, the ``announce.send`` button, the ``announcements`` row - is
    exactly as it was.
    """

    async def __call__(
        self, message: Message, settings: Settings | None = None, **_: object
    ) -> bool:
        if settings is None or not message.text:
            return False
        body = announcement_body(strip_bot_mention(message.text, bot_username_of(message)).strip())
        return len(split_announcement(body)) > 1


# --- Starting a multi-destination announcement ------------------------------
@router.message(MemberIntentFilter({MemberIntent.MAKE_ANNOUNCEMENT}), NeedsSplitting())
@router.message(MemberIntentFilter({MemberIntent.SEND_MULTI_ANNOUNCEMENT}))
async def handle_multi_send(
    message: Message,
    actor: Actor,
    settings: Settings,
    database: Database,
    member_text: str,
    request_id: uuid.UUID,
) -> None:
    """ "Gửi thông báo này vào các group Nội dung." - resolve, then show the list.

    Nothing durable is written for the *delivery* here. A draft row is created,
    which is not an outbox row: no group can receive anything until somebody
    has seen the exact destination list and confirmed it.
    """
    if message.bot is None or message.from_user is None:  # pragma: no cover
        return

    content = announcement_body(member_text)
    if not content:
        await formatting.answer(message, formatting.escape(ASK_FOR_CONTENT))
        return

    try:
        async with database.transaction() as session:
            dispatch = DispatchService(session, settings, AuditService(session))
            if not dispatch.may_broadcast(actor) and actor.user_id is None:
                await formatting.answer(message, formatting.escape(NO_PERMISSION))
                return

            resolution = await RecipientResolver(session, settings).resolve_audience(
                bot_identity=message.bot.id, actor=actor, text=member_text
            )
            card, version = await _open_from_resolution(
                session=session,
                settings=settings,
                actor=actor,
                message=message,
                content=content,
                original=member_text,
                resolution=resolution,
            )
    except SQLAlchemyError:
        # The transaction has already rolled back by the time this runs - the
        # context manager sees to that - so nothing partial is on disk. What
        # was missing before 0.6.0a3.post1 is this branch: the exception
        # escaped the handler, aiogram logged it, and the person who had just
        # written a department announcement got **no reply at all**.
        await _report_storage_failure(message, DRAFT_STORAGE_FAILED, request_id=request_id)
        return

    await _draw(message, card, settings=settings, version=version)


def announcement_body(text: str) -> str:
    """What the person wants said, with the addressing removed.

    Four readings, most explicit first: a quoted span, everything after a
    colon, everything after the first line when the first line is the
    instruction, and finally the words between the verb and the destination.
    A body that still comes out empty is asked for rather than guessed.
    """
    explicit = extract_send_content(text)
    if explicit:
        return explicit

    head, separator, tail = (text or "").partition("\n")
    if separator and tail.strip() and _looks_like_addressing(head):
        return tail.strip()

    stripped = _without_destination_clause(text)
    return stripped.strip()


def _looks_like_addressing(line: str) -> bool:
    """True when a line is "where to send it" rather than "what to send"."""
    from meobot.domain.dispatch.phrases import fold

    folded = fold(line)
    return any(word in folded for word in ("group", "nhom", "team", "tat ca", "toan bo"))


def _without_destination_clause(text: str) -> str:
    """Drop the "vào các group ..." clause, keeping everything else."""
    from meobot.domain.dispatch.phrases import GROUP_WORDS, fold

    words = (text or "").split()
    folded = [fold(word) for word in words]
    for index in range(len(words) - 1):
        if folded[index] in {"vao", "cho", "toi", "len", "den"} and (
            folded[index + 1] in GROUP_WORDS
            or folded[index + 1] in {"cac", "nhung", "moi", "tat", "toan"}
        ):
            lead = " ".join(words[:index]).strip(" .,:;-")
            head = lead.split(maxsplit=1)
            if len(head) == 2 and fold(head[0]) in {"gui", "nhan", "dang", "chuyen"}:
                lead = head[1]
            return lead
    return ""


async def _open_from_resolution(
    *,
    session: AsyncSession,
    settings: Settings,
    actor: Actor,
    message: Message,
    content: str,
    original: str,
    resolution: AudienceResolution,
) -> tuple[dispatch_cards.Card, int]:
    """Draft what a request resolved to, and pick the card that fits it.

    Three shapes, and the difference between them is how much checking the
    person still owes: destinations they *named* go straight to the preview,
    ones MeoBot *inferred* are read back first, and a whole-registry phrase is
    read back with the counts of what it left out.
    """
    if message.bot is None or message.from_user is None:  # pragma: no cover
        return dispatch_cards.Card(text=formatting.escape(NOT_FOUND), buttons=[]), 0

    drafts = DispatchDraftService(session, settings)
    service = DispatchService(session, settings, AuditService(session))

    if resolution.is_ambiguous:
        options = [row for _, rows in resolution.ambiguous for row in rows]
        permitted = await service.permitted_ids(actor, options)
        draft = await drafts.open(
            actor=actor,
            bot_identity=message.bot.id,
            source_chat_id=message.chat.id,
            original_text=original,
            rendered_text=content,
            candidates=[
                RecipientCandidate(chat=row, source=SelectionSource.NAMED) for row in options
            ],
            permitted_ids=permitted,
            preselect=False,
        )
        return (
            dispatch_cards.selection_card(await drafts.recipients(draft.id), heading=AMBIGUOUS),
            draft.version,
        )

    if not resolution.candidates:
        return _not_found_card(resolution), 0

    permitted = await service.permitted_ids(actor, list(resolution.chats))
    draft = await drafts.open(
        actor=actor,
        bot_identity=message.bot.id,
        source_chat_id=message.chat.id,
        original_text=original,
        rendered_text=content,
        candidates=resolution.candidates,
        permitted_ids=permitted,
        unresolved=resolution.unresolved,
        preselect=True,
    )
    rows = await drafts.recipients(draft.id)

    if resolution.scope is AudienceScope.ALL_REGISTERED:
        return (
            dispatch_cards.scope_card(
                usable=len(resolution.candidates),
                paused=resolution.paused_count,
                unreachable=resolution.unreachable_count,
                recipients=[row for row in rows if row.selected],
            ),
            draft.version,
        )
    if any(item.source is SelectionSource.INFERRED for item in resolution.candidates):
        phrase = next(
            (item.matched_phrase for item in resolution.candidates if item.matched_phrase),
            "nơi nhận bạn nói",
        )
        return (
            dispatch_cards.inferred_card(phrase, [row for row in rows if row.selected]),
            draft.version,
        )

    card = await _preview_card(session, settings, actor, draft, drafts)
    return card, draft.version


def _not_found_card(resolution: AudienceResolution) -> dispatch_cards.Card:
    """A destination that is not registered is a registry problem, and reads like one.

    Deliberately specific about the *words*: "chưa tìm thấy group phù hợp" left
    the owner guessing whether MeoBot had misread the name, lost the group, or
    was not connected at all - and a model filled that gap with instructions for
    setting up a bot that was already running.
    """
    quoted = ", ".join(f"“{name}”" for name in resolution.unresolved)
    lines = [
        formatting.escape(
            f"TasksBot chưa tìm thấy group {quoted} trong danh sách đã đăng ký."
            if quoted
            else NOT_FOUND
        ),
        "",
        formatting.escape(
            "Bạn xem danh sách group đã đăng ký, hoặc vào chính group đó và nhắn "
            "“đăng ký group này” giúp mình nhé."
        ),
    ]
    return dispatch_cards.Card(
        text="\n".join(lines),
        buttons=[
            [ButtonSpec("👥 Xem các group", "disp.list.each")],
            [ButtonSpec(copy.Button.DISCARD.value, "disp.cancel")],
        ],
    )


# --- The registered-group list ----------------------------------------------
@router.message(
    MemberIntentFilter({MemberIntent.VIEW_REGISTERED_CHATS, MemberIntent.CHOOSE_DESTINATIONS})
)
async def handle_list_destinations(
    message: Message, actor: Actor, settings: Settings, database: Database
) -> None:
    """ "Có những group nào?" - scoped to what this person may actually use.

    Not a display filter. Knowing that a "Ban giám đốc" group exists is itself
    information, and a group assignment does not grant it.
    """
    if message.bot is None:  # pragma: no cover
        return
    async with database.session() as session:
        rows = await ChatRegistryService(session).visible_to(
            actor=actor, bot_identity=message.bot.id
        )
    if not rows:
        await formatting.answer(
            message,
            formatting.escape(
                dispatch_cards.NOTHING_REGISTERED
                if DispatchService.may_broadcast(actor)
                else dispatch_cards.NOTHING_VISIBLE
            ),
        )
        return
    await _draw(message, dispatch_cards.registry_list(rows), settings=settings)


# --- Buttons ----------------------------------------------------------------
@router.callback_query(F.data.regexp(data_pattern(DISPATCH_ACTIONS)))
async def handle_dispatch_button(
    query: CallbackQuery,
    actor: Actor,
    settings: Settings,
    database: Database,
    request_id: uuid.UUID,
) -> None:
    """Every press this router draws, verified against the person who got it."""
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
        # Signed for a different person, chat or bot. Another owner's card must
        # not be usable by whoever can see it.
        await query.answer(copy.Problem.BUTTON_NOT_YOURS.value, show_alert=True)
        return
    if payload.is_expired(utcnow()):
        await formatting.edit_callback(query, formatting.escape(copy.Problem.BUTTON_EXPIRED.value))
        return

    try:
        async with database.transaction() as session:
            drafts = DispatchDraftService(session, settings)
            service = DispatchService(session, settings, AuditService(session))

            if payload.action == "disp.retry":
                await _retry(query, session, settings, actor, payload.entity_id)
                return
            if payload.action == "disp.summary":
                await _detail(query, service, payload.entity_id)
                return

            if payload.action in {"disp.list.all", "disp.list.each", "disp.list.healthy"}:
                await _pick_from_registry(
                    query,
                    session=session,
                    settings=settings,
                    actor=actor,
                    mode=payload.action,
                )
                return

            draft = await drafts.active(
                bot_identity=query.bot.id,
                source_chat_id=query.message.chat.id,
                telegram_user_id=query.from_user.id,
            )
            if payload.action == "disp.cancel":
                if draft is not None:
                    await drafts.cancel(draft)
                await formatting.edit_callback(query, formatting.escape(copy.CANCELLED))
                return
            if draft is None:
                await formatting.edit_callback(
                    query, formatting.escape(dispatch_cards.DRAFT_EXPIRED)
                )
                return

            card = await _apply_button(
                query=query,
                session=session,
                settings=settings,
                actor=actor,
                request_id=request_id,
                draft=draft,
                drafts=drafts,
                service=service,
                action=payload.action,
                entity_id=payload.entity_id,
                pressed_version=payload.version,
            )
            if card is None:
                return
            version = draft.version
    except SQLAlchemyError:
        # A press that writes nothing and says nothing is the worst of both:
        # the person believes they have sent something and has no way to find
        # out otherwise. The rollback already happened; this is the reply.
        logger.exception(
            "dispatch_button_persistence_failed", extra={"request_id": str(request_id)}
        )
        await formatting.edit_callback(
            query,
            formatting.escape(
                CONFIRM_STORAGE_FAILED if payload.action == "disp.send" else DRAFT_STORAGE_FAILED
            ),
        )
        return

    if isinstance(query.message, Message):
        await _draw(
            query.message,
            card,
            settings=settings,
            telegram_user_id=query.from_user.id,
            version=version,
        )


async def _apply_button(
    *,
    query: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
    actor: Actor,
    request_id: uuid.UUID,
    draft: MessageDispatchDraft,
    drafts: DispatchDraftService,
    service: DispatchService,
    action: str,
    entity_id: uuid.UUID | None,
    pressed_version: int,
) -> dispatch_cards.Card | None:
    """One press, applied to the draft it belongs to.

    ``pressed_version`` is the draft version the card was *drawn* against.
    Only confirmation checks it, and deliberately so: ticking a group off a
    slightly stale keyboard is harmless and re-renders correctly, while sending
    from a stale preview means somebody confirmed a destination list that is no
    longer the one about to be used.
    """
    if action == "disp.select" and entity_id is not None:
        await drafts.set_selected(draft, recipient_id=entity_id, selected=True)
        return dispatch_cards.selection_card(await drafts.recipients(draft.id))
    if action == "disp.deselect" and entity_id is not None:
        await drafts.set_selected(draft, recipient_id=entity_id, selected=False)
        return dispatch_cards.selection_card(await drafts.recipients(draft.id))
    if action == "disp.all":
        await drafts.select_all(draft)
        return dispatch_cards.selection_card(await drafts.recipients(draft.id))
    if action == "disp.none":
        await drafts.select_none(draft)
        return dispatch_cards.selection_card(await drafts.recipients(draft.id))
    if action == "disp.reselect":
        return await _reselect(session, settings, actor, draft, drafts, message=query.message)
    if action == "disp.edit":
        await drafts.cancel(draft)
        await formatting.edit_callback(query, formatting.escape(REWRITE_HELP))
        return None
    if action == "disp.detail":
        return await _draft_detail(drafts, draft)
    if action == "disp.done":
        return await _preview_card(session, settings, actor, draft, drafts)
    if action == "disp.send":
        if entity_id is not None and entity_id != draft.id:
            # A button from a draft that has since been replaced.
            await formatting.edit_callback(query, formatting.escape(dispatch_cards.DRAFT_EXPIRED))
            return None
        if pressed_version and pressed_version != draft.version:
            # The selection changed after this card was drawn. Re-show it
            # rather than send: what the person read is not what would go out.
            await formatting.edit_callback(query, formatting.escape(dispatch_cards.SELECTION_MOVED))
            return await _preview_card(session, settings, actor, draft, drafts)
        return await _confirm(
            message=query.message if isinstance(query.message, Message) else None,
            session=session,
            settings=settings,
            actor=actor,
            request_id=request_id,
            draft=draft,
            drafts=drafts,
            query=query,
        )
    return None


async def _pick_from_registry(
    query: CallbackQuery,
    *,
    session: AsyncSession,
    settings: Settings,
    actor: Actor,
    mode: str,
) -> None:
    """The three answers to "Bạn muốn gửi tới đâu?" on the registry card.

    All three only *choose destinations*. None of them creates a draft, because
    at this point nobody has written an announcement yet - the content is what
    the next message supplies.
    """
    if query.bot is None:  # pragma: no cover
        return
    rows = await ChatRegistryService(session).visible_to(actor=actor, bot_identity=query.bot.id)
    if mode == "disp.list.healthy":
        rows = [row for row in rows if row.bot_can_send and row.health_status.is_healthy]
    if not rows:
        await formatting.edit_callback(query, formatting.escape(dispatch_cards.NOTHING_REGISTERED))
        return

    names = "\n".join(f"• {row.display_name}" for row in rows)
    hint = (
        "Bạn nhắn nội dung thông báo kèm nơi nhận giúp mình nhé.\n"
        "Ví dụ: “Gửi thông báo này cho tất cả group: <nội dung>.”"
    )
    await formatting.edit_callback(
        query, formatting.escape(f"👥 Group bạn có thể gửi:\n\n{names}\n\n{hint}")
    )


async def _reselect(
    session: AsyncSession,
    settings: Settings,
    actor: Actor,
    draft: MessageDispatchDraft,
    drafts: DispatchDraftService,
    *,
    message: object,
) -> dispatch_cards.Card:
    """Start the recipient list again, keeping what somebody wrote.

    The words are the expensive part of a draft. Changing who reads them never
    costs the author their message.
    """
    rows = await ChatRegistryService(session).visible_to(
        actor=actor, bot_identity=draft.bot_identity
    )
    service = DispatchService(session, settings, AuditService(session))
    permitted = await service.permitted_ids(actor, rows)
    await drafts.replace_candidates(
        draft,
        [RecipientCandidate(chat=row, source=SelectionSource.BUTTON) for row in rows],
        permitted_ids=permitted,
        preselect=False,
    )
    return dispatch_cards.selection_card(await drafts.recipients(draft.id))


async def _draft_detail(
    drafts: DispatchDraftService, draft: MessageDispatchDraft
) -> dispatch_cards.Card:
    """Why each destination is on the list - named, inferred, or ticked by hand."""
    rows = await drafts.recipients(draft.id)
    lines = [formatting.bold("Nơi nhận và lý do"), ""]
    for row in rows:
        mark = "☑️" if row.selected else "⬜"
        lines.append(formatting.escape(f"{mark} {row.display_name}"))
        lines.append(formatting.escape(f"   {dispatch_cards.source_label(row.selection_source)}"))
        if not row.permitted:
            lines.append(formatting.escape("   Bạn chưa được phép gửi vào group này"))
    return dispatch_cards.Card(
        text="\n".join(lines), buttons=dispatch_cards.selection_keyboard(rows)
    )


async def _preview_card(
    session: AsyncSession,
    settings: Settings,
    actor: Actor,
    draft: MessageDispatchDraft,
    drafts: DispatchDraftService,
) -> dispatch_cards.Card:
    """The full preview, and the transition that makes "Xác nhận" mean it."""
    selected = await drafts.selected(draft.id)
    if not selected:
        return dispatch_cards.Card(
            text=formatting.escape(dispatch_cards.NO_SELECTION),
            buttons=dispatch_cards.selection_keyboard(await drafts.recipients(draft.id)),
        )
    await drafts.mark_preview(draft)

    chats = await _chats_of(session, selected)
    parts = split_announcement(draft.rendered_text)
    excluded = [row.display_name for row in await drafts.recipients(draft.id) if not row.selected]
    unresolved = (draft.unresolved_phrases or "").splitlines()
    return dispatch_cards.preview(
        content=draft.rendered_text,
        recipients=selected,
        sender_label=role_label(actor.role),
        health_lines=dispatch_cards.health_summary(chats),
        excluded=excluded,
        unresolved=[name for name in unresolved if name],
        part_count=len(parts),
        draft_id=str(draft.id),
    )


async def _confirm(
    *,
    message: Message | None,
    session: AsyncSession,
    settings: Settings,
    actor: Actor,
    request_id: uuid.UUID,
    draft: MessageDispatchDraft,
    drafts: DispatchDraftService,
    query: CallbackQuery | None = None,
) -> dispatch_cards.Card | None:
    """Turn the draft into one dispatch and N durable deliveries.

    A typed "Xác nhận" and a pressed "✅ Gửi tới 3 group" arrive here by
    different routes and do exactly the same thing - which is the point of
    :meth:`DispatchService.confirm` being idempotent: whichever lands second
    finds the dispatch the first one created rather than building a second.
    """
    service = DispatchService(session, settings, AuditService(session))
    selected = await drafts.selected(draft.id)
    if not selected:
        return dispatch_cards.Card(
            text=formatting.escape(dispatch_cards.NO_SELECTION),
            buttons=dispatch_cards.selection_keyboard(await drafts.recipients(draft.id)),
        )

    try:
        dispatch, created = await service.confirm(actor=actor, request_id=request_id, draft=draft)
    except MeoBotError as exc:
        text = "⛔ " + formatting.escape(exc.message)
        await _reply(message, query, text)
        return None

    registry = ChatRegistryService(session)
    for row in await service.recipients(dispatch.id):
        await registry.mark_used(row.telegram_chat_row_id)

    count = dispatch.recipient_count
    text = formatting.escape(
        dispatch_cards.queued(count)
        if created
        else "Thông báo này đã được xếp hàng gửi trước đó rồi."
    )
    await _reply(message, query, text)
    return None


async def _retry(
    query: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
    actor: Actor,
    dispatch_id: uuid.UUID | None,
) -> None:
    """Queue the destinations that failed, and only those."""
    if dispatch_id is None:  # pragma: no cover - button always carries one
        return
    service = DispatchService(session, settings, AuditService(session))
    dispatch = await service.by_id(dispatch_id)
    if dispatch is None:
        await formatting.edit_callback(query, formatting.escape(dispatch_cards.DRAFT_EXPIRED))
        return
    destinations, retried_parts = await service.retry_failed(actor=actor, dispatch=dispatch)
    if not destinations:
        await formatting.edit_callback(
            query, formatting.escape("Không còn nơi nhận nào cần gửi lại.")
        )
        return
    logger.info(
        "dispatch_retry_requested",
        extra={"dispatch_id": str(dispatch_id), "parts": retried_parts},
    )
    await formatting.edit_callback(
        query,
        formatting.escape(f"🔄 Đã xếp lại {destinations} group để TasksBot gửi tiếp."),
    )


async def _detail(
    query: CallbackQuery, service: DispatchService, dispatch_id: uuid.UUID | None
) -> None:
    """Every destination's own outcome, spelled out."""
    if dispatch_id is None:  # pragma: no cover
        return
    outcomes = await service.outcomes(dispatch_id)
    if not outcomes:
        await formatting.edit_callback(query, formatting.escape(dispatch_cards.DRAFT_EXPIRED))
        return
    card = dispatch_cards.result_card(
        [(item.display_name, item.status, "") for item in outcomes],
        dispatch_id=str(dispatch_id),
    )
    await formatting.edit_callback(query, card.text)


# --- Plumbing ---------------------------------------------------------------
async def _chats_of(
    session: AsyncSession, rows: Sequence[MessageDispatchDraftRecipient]
) -> list[TelegramChat]:
    """The registrations behind a draft's candidate rows, in card order."""
    registry = ChatRegistryService(session)
    found: list[TelegramChat] = []
    for row in rows:
        chat = await registry.by_id(row.recipient_chat_row_id)
        if chat is not None:
            found.append(chat)
    return found


async def _draw(
    message: Message,
    card: dispatch_cards.Card,
    *,
    settings: Settings,
    telegram_user_id: int | None = None,
    version: int = 0,
) -> None:
    """Send one card, with its buttons signed against this person and chat.

    ``telegram_user_id`` has to be passed explicitly when the card follows a
    button press: ``query.message`` is MeoBot's own message, so its
    ``from_user`` is the bot. Signing against that would produce a keyboard
    nobody could use - and, worse, one whose binding said "the bot".

    ``version`` is the draft version the card was drawn against, signed into
    every button on it. It is what makes a stale preview harmless: a card that
    said "Gửi tới 3 group" cannot confirm a draft that has since become five,
    because the signature no longer verifies against the current version.
    """
    if message.bot is None:  # pragma: no cover
        return
    presser = telegram_user_id or (message.from_user.id if message.from_user else None)
    if presser is None:  # pragma: no cover
        return
    binding = member_keyboards.binding_for(
        bot_id=message.bot.id,
        telegram_user_id=presser,
        chat_id=message.chat.id,
    )
    await formatting.answer(
        message,
        card.text,
        reply_markup=member_keyboards.render(
            card.buttons, settings=settings, binding=binding, version=version
        ),
    )


async def _reply(message: Message | None, query: CallbackQuery | None, text: str) -> None:
    """Answer wherever this turn came from - a message or a pressed button."""
    if query is not None:
        await formatting.edit_callback(query, text)
        return
    if message is not None:
        await formatting.answer(message, text)
