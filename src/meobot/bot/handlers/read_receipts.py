"""The "✅ Đã đọc" button, and the questions it makes answerable.

Three things happen here, and each one is shaped by a limitation worth stating
plainly: **Telegram will not tell a bot who is in a group.** There is no API for
it. So MeoBot cannot compute "everybody who should have read this" from the
platform, and any list it produced that way would be a guess.

What it does instead:

* the acknowledgement records **who Telegram says pressed the button**, which
  is unforgeable and cannot be somebody acting on a colleague's behalf;
* the denominator comes from a snapshot taken at publication time, from
  configuration - department-wide, or the people explicitly assigned to that
  group;
* where no audience is configured, MeoBot reports the count it has and says it
  cannot name who is missing. That is the true answer, and far more useful than
  a confident wrong list.

The shared group message is never edited into a per-user state. One message is
seen by everybody; rewriting it to say "bạn đã xác nhận" would say that to
everybody.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from meobot.application.announcement_audience_service import AnnouncementAudienceService
from meobot.application.announcement_service import AnnouncementService
from meobot.application.audit_service import AuditService
from meobot.application.notification_router import (
    NotificationRouter,
    RouteRequest,
    announcement_reminder_key,
)
from meobot.application.recipient_resolver import RecipientResolver
from meobot.bot import formatting
from meobot.bot.announcement_keyboards import receipt_binding
from meobot.bot.member_filters import MemberIntentFilter
from meobot.core.config import Settings
from meobot.core.logging import get_logger
from meobot.core.time import utcnow
from meobot.db.models.notifications import Announcement
from meobot.db.session import Database
from meobot.domain.identity.models import Actor
from meobot.domain.member.callbacks import MemberBinding, build, data_pattern, parse
from meobot.domain.member.intents import MemberIntent
from meobot.domain.notifications.models import AnnouncementStatus, NotificationEvent

logger = get_logger(__name__)

router = Router(name="read_receipts")

RECEIPT_ACTIONS = ("announce.ack", "announce.ask")
REMIND_ACTIONS = ("announce.remind",)

HANDLED: frozenset[MemberIntent] = frozenset(
    {MemberIntent.WHO_HAS_NOT_READ, MemberIntent.REMIND_UNREAD}
)

#: The short toast the presser sees. Deliberately says "đã ghi nhận" only
#: because a row exists by the time it is shown.
ACKNOWLEDGED = "MeoBot đã ghi nhận bạn đã đọc thông báo."
ALREADY_ACKNOWLEDGED = "Bạn đã xác nhận thông báo này rồi."
NEEDS_FOLLOWUP = "MeoBot đã ghi nhận. Trưởng phòng sẽ trao đổi thêm với bạn."
STALE = "Nút này không còn hiệu lực."
NOTHING_TO_REPORT = "Chưa có thông báo nào cần theo dõi xác nhận."
NOT_PERMITTED = "Bạn chưa được phép xem xác nhận đã đọc của thông báo này."
NOBODY_OUTSTANDING = "Tất cả mọi người đã xác nhận. Không cần nhắc ai cả."


@router.callback_query(F.data.regexp(data_pattern(RECEIPT_ACTIONS)))
async def handle_read_receipt(
    query: CallbackQuery,
    database: Database,
    settings: Settings,
    actor: Actor | None = None,
) -> None:
    """Record one person's confirmation. Idempotent, and bound to them.

    ``actor`` is optional: somebody in a group who has read an announcement may
    confirm it whether or not they are a registered user. What they cannot do
    is confirm for anybody else - the identity comes from
    ``callback_query.from_user``, which Telegram supplies and a forwarded
    button cannot change.
    """
    chat_id = query.message.chat.id if isinstance(query.message, Message) else 0
    binding = receipt_binding(bot_id=query.bot.id if query.bot is not None else 0, chat_id=chat_id)
    payload = parse(query.data or "", secret=settings.callback_secret, binding=binding)
    if payload is None or payload.entity_id is None or payload.is_expired(utcnow()):
        await query.answer(STALE, show_alert=True)
        return

    needs_followup = payload.action == "announce.ask"
    async with database.transaction() as session:
        service = AnnouncementService(session, settings, AuditService(session))
        announcement = await session.get(Announcement, payload.entity_id)
        if announcement is None or announcement.status is not AnnouncementStatus.PUBLISHED:
            await query.answer(STALE, show_alert=True)
            return
        _row, created = await service.acknowledge(
            announcement_id=payload.entity_id,
            telegram_user_id=query.from_user.id,
            user_id=actor.user_id if actor is not None else None,
            display_name=query.from_user.full_name,
            needs_followup=needs_followup,
        )
        await AnnouncementAudienceService(session).mark_acknowledged(
            announcement_id=payload.entity_id, telegram_user_id=query.from_user.id
        )

    # A toast, not an edit. The message belongs to the whole group; rewriting
    # it for one reader would rewrite it for all of them.
    if needs_followup:
        await query.answer(NEEDS_FOLLOWUP, show_alert=True)
    else:
        await query.answer(ACKNOWLEDGED if created else ALREADY_ACKNOWLEDGED)


@router.message(MemberIntentFilter(HANDLED))
async def handle_receipt_question(
    message: Message,
    actor: Actor,
    database: Database,
    settings: Settings,
    member_intent: MemberIntent,
    member_text: str,
) -> None:
    """ "Ai chưa đọc thông báo?" and "Nhắc những người chưa đọc."."""
    async with database.session() as session:
        announcement = await _latest_published(session, settings, actor)
        if announcement is None:
            await formatting.answer(message, formatting.escape(NOTHING_TO_REPORT))
            return
        if not await _may_view(session, settings, actor, announcement):
            await formatting.answer(message, formatting.escape(NOT_PERMITTED))
            return
        report = await AnnouncementAudienceService(session).report(announcement.id)
        announcement_id = announcement.id
        outstanding = len(report.outstanding)

    if member_intent is MemberIntent.WHO_HAS_NOT_READ:
        await formatting.answer(message, formatting.escape(report.render()))
        return

    if not report.audience_known:
        await formatting.answer(message, formatting.escape(report.render()))
        return
    if not outstanding:
        await formatting.answer(message, formatting.escape(NOBODY_OUTSTANDING))
        return

    # A fan-out gets a preview and a confirmation, like every other message
    # MeoBot sends on somebody's behalf. A reminder that reaches eighty people
    # by accident cannot be recalled.
    capped = min(outstanding, settings.announcement_max_private_reminders)
    lines = [
        "🔔 " + formatting.bold("NHẮC NGƯỜI CHƯA XÁC NHẬN"),
        "",
        formatting.escape(f"Số người sẽ được nhắc riêng: {capped}"),
    ]
    if capped < outstanding:
        lines.append(
            formatting.escape(
                f"MeoBot chỉ nhắc {capped} người trong lần này, còn {outstanding - capped} "
                "người sẽ nhắc ở lần sau."
            )
        )
    lines.extend(["", formatting.escape("Bạn kiểm tra lại trước khi xác nhận nhé.")])

    await formatting.answer(
        message,
        "\n".join(lines),
        reply_markup=formatting.keyboard(
            [
                [
                    (
                        "✅ Gửi nhắc",
                        build(
                            "announce.remind",
                            secret=settings.callback_secret,
                            binding=_binding(message),
                            expires_at=utcnow()
                            + timedelta(seconds=settings.member_flow_ttl_seconds),
                            entity_id=announcement_id,
                        ),
                    )
                ]
            ]
        ),
    )


@router.callback_query(F.data.regexp(data_pattern(REMIND_ACTIONS)))
async def handle_remind_unread(
    query: CallbackQuery,
    actor: Actor,
    database: Database,
    settings: Settings,
) -> None:
    """Queue one private reminder per outstanding recipient, bounded."""
    await query.answer()
    chat_id = query.message.chat.id if isinstance(query.message, Message) else 0
    payload = parse(
        query.data or "",
        secret=settings.callback_secret,
        binding=MemberBinding(
            bot_id=query.bot.id if query.bot is not None else 0,
            telegram_user_id=query.from_user.id,
            chat_id=chat_id,
        ),
    )
    if payload is None or payload.entity_id is None or payload.is_expired(utcnow()):
        await formatting.edit_callback(query, formatting.escape(STALE))
        return

    async with database.transaction() as session:
        announcement = await session.get(Announcement, payload.entity_id)
        if announcement is None:
            await formatting.edit_callback(query, formatting.escape(STALE))
            return
        if not await _may_view(session, settings, actor, announcement):
            await formatting.edit_callback(query, formatting.escape(NOT_PERMITTED))
            return

        audience = AnnouncementAudienceService(session)
        outstanding = await audience.unread_recipients(
            announcement.id, limit=settings.announcement_max_private_reminders
        )
        resolver = RecipientResolver(session, settings)
        requests: list[RouteRequest] = []
        unreachable = 0
        for recipient in outstanding:
            destination = await resolver.private_destination(user_id=recipient.user_id)
            if not destination.is_resolved or destination.telegram_chat_id is None:
                unreachable += 1
                continue
            requests.append(
                RouteRequest(
                    event_type=NotificationEvent.ANNOUNCEMENT_REMINDER,
                    template_key="announcement.reminder",
                    payload={"excerpt": announcement.content[:200]},
                    # One reminder per (announcement, person), whatever happens.
                    idempotency_key=announcement_reminder_key(
                        announcement.id, recipient.telegram_user_id or 0
                    ),
                    aggregate_type="announcement",
                    aggregate_id=announcement.id,
                    recipient_user_id=recipient.user_id,
                    private_chat_id=destination.telegram_chat_id,
                    created_by_user_id=actor.user_id,
                    destination_label="Chat riêng",
                    business_summary="Nhắc xác nhận đã đọc thông báo",
                )
            )
            recipient.reminder_sent_at = utcnow()

        result = await NotificationRouter(session, settings).route(requests)
        queued = len(result.queued)

    # "Đã xếp hàng gửi", not "đã gửi": the rows exist, the worker has not run.
    lines = [formatting.escape(f"📨 MeoBot đã xếp hàng gửi nhắc cho {queued} người.")]
    if unreachable:
        lines.append(
            formatting.escape(
                f"{unreachable} người chưa bắt đầu trò chuyện với MeoBot nên chưa nhắn riêng được."
            )
        )
    await formatting.edit_callback(query, "\n".join(lines))


def _binding(message: Message) -> MemberBinding:
    return MemberBinding(
        bot_id=message.bot.id if message.bot is not None else 0,
        telegram_user_id=message.from_user.id if message.from_user is not None else 0,
        chat_id=message.chat.id,
    )


async def _latest_published(
    session: object, settings: Settings, actor: Actor
) -> Announcement | None:
    """The most recent published announcement this person authored.

    Scoped to the author rather than "the newest anywhere": read receipts are
    about a message somebody sent, and showing one person the audience of
    another person's announcement is a disclosure nobody asked for.
    """
    from sqlalchemy import select

    result = await session.execute(  # type: ignore[attr-defined]
        select(Announcement)
        .where(
            Announcement.status == AnnouncementStatus.PUBLISHED,
            Announcement.created_by_user_id == actor.user_id,
        )
        .order_by(Announcement.confirmed_at.desc())
        .limit(1)
    )
    row = result.scalar_one_or_none()
    return row if isinstance(row, Announcement) else None


async def _may_view(
    session: object, settings: Settings, actor: Actor, announcement: Announcement
) -> bool:
    """Whether this actor may see who has read this announcement.

    The author always may. A Trưởng nhóm may for a group they were explicitly
    given ``can_view_read_receipts`` on - which is a separate grant from being
    able to broadcast there, because seeing who has not read something is a
    different kind of visibility from being able to write to them.
    """
    from meobot.application.chat_assignment_service import ChatAssignmentService

    author = announcement.created_by_user_id
    if author is not None and author == actor.user_id:
        return True
    if AnnouncementService.may_broadcast(actor):
        return True
    if announcement.destination_chat_id is None or actor.user_id is None:
        return False
    assignment = await ChatAssignmentService(session).assignment_for(  # type: ignore[arg-type]
        chat_row_id=announcement.destination_chat_id, user_id=actor.user_id
    )
    return assignment is not None and assignment.is_active and assignment.can_view_read_receipts


def announcement_id_of(payload_entity: uuid.UUID | None) -> uuid.UUID | None:
    """Narrowing helper kept for readability at the call sites above."""
    return payload_entity
