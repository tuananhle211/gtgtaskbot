"""The owner's approval buttons, and the member's "ask for more quota" button.

Every press goes through the same four checks before anything happens:

1. **Who pressed it.** Only the configured owner. The acting Telegram id is
   part of the signature material, so a forwarded button fails for anyone else
   rather than merely being refused by a check somebody could remove.
2. **Is it authentic.** The binding - owner, subject, chat, source message - is
   re-read from the stored request and fed back into the HMAC. Nothing about
   *who* or *where* travels in the callback data, so nothing about who or where
   can be tampered with.
3. **Is it still live.** The expiry is inside the signature and checked
   explicitly; an old button in an old chat cannot be pressed back to life.
4. **Has it already been decided.** Resolving a request is idempotent, so a
   double tap - or a second owner device - grants exactly once.

Only then does the action run.
"""

from __future__ import annotations

import uuid

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.access_notifications_service import (
    queue_quota_decision_notification,
    queue_quota_request_notification,
)
from meobot.application.access_request_service import AccessRequestService
from meobot.application.audit_service import AuditService
from meobot.application.deferred_guest_service import DeferredGuestMessageService
from meobot.application.group_policy_service import GroupPolicyService, guest_deadline_text
from meobot.application.quota_service import QuotaService
from meobot.application.user_service import UserService
from meobot.bot import formatting
from meobot.bot.access_notifications import (
    QUOTA_REQUEST_CALLBACK,
    binding_for,
    confirm_member_keyboard,
)
from meobot.core.config import Settings
from meobot.core.errors import MeoBotError
from meobot.core.logging import get_logger
from meobot.core.time import utcnow
from meobot.db.models.access import PendingGuestAccessRequest
from meobot.db.session import Database
from meobot.domain.access.callbacks import (
    ACCESS_KIND,
    QUOTA_KIND,
    CallbackBinding,
    parse_access_callback,
    peek_request_id,
)
from meobot.domain.access.models import (
    GUEST_DEFAULT_DURATION,
    GUEST_DEFAULT_QUESTION_LIMIT,
    AccessAction,
    GroupPolicyMode,
    PendingRequestStatus,
    QuotaAction,
)
from meobot.domain.access.quota import MEMBER_DEFAULT_DAILY_LIMIT, QUOTA_BONUS_STEP
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.deferred.models import AuthorizationMode
from meobot.domain.identity.labels import role_label
from meobot.domain.identity.models import Actor, Role

logger = get_logger(__name__)

router = Router(name="access")

STALE_BUTTON = "Nút này không còn hiệu lực."
NOT_OWNER = f"Chỉ {role_label(Role.OWNER)} được dùng nút này."
ALREADY_DECIDED = "Yêu cầu này đã được xử lý rồi."

#: The standing limit behind the owner's "raise their limit" button.
CUSTOM_DAILY_LIMIT = 50


def _is_owner(query: CallbackQuery, settings: Settings, actor: Actor | None) -> bool:
    """Only the configured owner may decide who gets in.

    Both facts are required: the role MeoBot resolved *and* the configured
    Telegram id. Either alone would be enough in normal operation; requiring
    both means a misconfiguration cannot silently widen who can grant access.
    """
    if query.from_user is None or settings.meobot_owner_telegram_id is None:
        return False
    if query.from_user.id != settings.meobot_owner_telegram_id:
        return False
    return actor is not None and actor.role is Role.OWNER


@router.callback_query(F.data.startswith(f"{ACCESS_KIND}|"))
async def handle_access_decision(
    query: CallbackQuery,
    database: Database,
    settings: Settings,
    request_id: uuid.UUID,
    actor: Actor | None = None,
) -> None:
    """Apply one of the owner's five decisions about a stranger."""
    await query.answer()
    data = query.data or ""

    if not _is_owner(query, settings, actor):
        await query.answer(NOT_OWNER, show_alert=True)
        return
    owner = actor
    if owner is None:  # pragma: no cover - guaranteed by _is_owner
        return

    peeked = peek_request_id(data)
    if peeked is None:
        await formatting.edit_callback(query, formatting.escape(STALE_BUTTON))
        return
    _, pending_id = peeked

    async with database.transaction() as session:
        requests = AccessRequestService(session)
        request = await requests.by_id(pending_id)
        if request is None:
            await formatting.edit_callback(query, formatting.escape(STALE_BUTTON))
            return

        payload = parse_access_callback(
            data,
            secret=settings.callback_secret,
            binding=binding_for(request, owner_telegram_id=query.from_user.id),
        )
        if payload is None or payload.is_expired(utcnow()):
            await formatting.edit_callback(query, formatting.escape(STALE_BUTTON))
            return

        action = payload.action
        if not isinstance(action, AccessAction):  # pragma: no cover - kind-checked
            return

        # The preview step is the only one that leaves the request open.
        if action is AccessAction.ADD_AS_MEMBER:
            await _render_member_preview(query, request, settings=settings)
            return

        if request.status is not PendingRequestStatus.OPEN:
            await formatting.edit_callback(query, formatting.escape(ALREADY_DECIDED))
            return

        summary = await _apply(
            action,
            session=session,
            request=request,
            owner=owner,
            settings=settings,
            request_id=request_id,
        )
        await requests.resolve(request, actor=owner, action=action)

        # The decision and the intent to act on it commit together. Before
        # 0.6.0a2 the answer was generated inside this handler and sent
        # directly, so the owner waited on a model and a crash lost the reply
        # while keeping the grant.
        deferred_id = await _settle_deferred_question(
            session, settings, request=request, action=action
        )

    await formatting.edit_callback(query, summary)

    if deferred_id is not None:
        # Durable, asynchronous, and idempotent: the task claims the question
        # with one conditional UPDATE, so a second press generates nothing.
        enqueue_deferred_guest_reply(deferred_id)


async def _render_member_preview(
    query: CallbackQuery, request: PendingGuestAccessRequest, *, settings: Settings
) -> None:
    """Show what adding this person would do, before doing it.

    Creating an account is the highest-risk action on the menu, so it takes two
    presses: this one only describes the consequence.
    """
    body = "\n".join(
        [
            "⚠️ " + formatting.bold("Xác nhận thêm thành viên"),
            "",
            formatting.escape(f"Người dùng: {request.requester_display_name or 'không rõ'}"),
            formatting.escape(f"Vai trò sẽ cấp: {role_label(Role.EMPLOYEE)}"),
            formatting.escape(f"Hạn mức trò chuyện: {MEMBER_DEFAULT_DAILY_LIMIT} lượt/ngày"),
            "",
            formatting.escape(
                "Người này sẽ trở thành thành viên hệ thống, dùng được cả chat riêng "
                "và các lệnh nghiệp vụ theo quyền Member. Lượt Guest chưa dùng sẽ "
                "không được chuyển sang."
            ),
        ]
    )
    await formatting.edit_callback(query, body)
    if isinstance(query.message, Message):
        await query.message.edit_reply_markup(
            reply_markup=confirm_member_keyboard(
                request, settings=settings, owner_telegram_id=query.from_user.id
            )
        )


async def _apply(
    action: AccessAction,
    *,
    session: AsyncSession,
    request: PendingGuestAccessRequest,
    owner: Actor,
    settings: Settings,
    request_id: uuid.UUID,
) -> str:
    """Run one decision and return the line to show the owner."""
    policy = GroupPolicyService(session)
    audit = AuditService(session)
    name = request.requester_display_name or str(request.requester_telegram_id)

    if action is AccessAction.ANSWER_ONCE:
        await audit.record_action(
            request_id=request_id,
            actor=owner,
            action=AuditAction.GUEST_ANSWER_ONCE.value,
            result=AuditResult.SUCCESS,
            entity_type="access_request",
            entity_id=str(request.id),
            after_data={
                "target_telegram_id": request.requester_telegram_id,
                "chat_id": request.telegram_chat_id,
                "scope": "single_message",
            },
        )
        return formatting.escape(f"💬 Đã trả lời một lần cho {name}. Không cấp thêm quyền nào.")

    if action is AccessAction.GRANT_GUEST:
        row = await policy.grant_guest(
            actor=owner,
            bot_id=request.bot_id,
            chat_id=request.telegram_chat_id,
            telegram_user_id=request.requester_telegram_id,
            question_limit=GUEST_DEFAULT_QUESTION_LIMIT,
            duration=GUEST_DEFAULT_DURATION,
        )
        await audit.record_action(
            request_id=request_id,
            actor=owner,
            action=AuditAction.GUEST_ACCESS_GRANTED.value,
            result=AuditResult.SUCCESS,
            entity_type="group_member_policy",
            entity_id=str(row.id),
            after_data={
                "target_telegram_id": request.requester_telegram_id,
                "chat_id": request.telegram_chat_id,
                "question_limit": row.guest_question_limit,
                "expires_at": row.guest_expires_at.isoformat() if row.guest_expires_at else None,
            },
        )
        deadline = guest_deadline_text(row, settings.timezone)
        return formatting.escape(
            f"⏱ {name} là Guest trong group này: {row.guest_question_limit} lượt, "
            f"hết hạn {deadline}."
        )

    if action is AccessAction.CONFIRM_MEMBER:
        users = UserService(session, audit)
        try:
            user = await users.add_user(
                actor=owner,
                request_id=request_id,
                telegram_user_id=request.requester_telegram_id,
                role=Role.EMPLOYEE,
                full_name=request.requester_display_name or "",
                telegram_username=request.requester_username,
            )
        except MeoBotError as exc:
            return "⛔ " + formatting.escape(exc.message)
        # Membership replaces the Guest allowance; unused questions are not
        # carried over into the member's daily quota.
        await policy.reset_to_inherit(
            actor=owner,
            bot_id=request.bot_id,
            chat_id=request.telegram_chat_id,
            telegram_user_id=request.requester_telegram_id,
        )
        return formatting.escape(
            f"✅ Đã thêm {user.full_name} với vai trò {role_label(Role.EMPLOYEE)}."
        )

    if action is AccessAction.IGNORE_ONCE:
        await audit.record_action(
            request_id=request_id,
            actor=owner,
            action=AuditAction.ACCESS_REQUEST_RESOLVED.value,
            result=AuditResult.DENIED,
            entity_type="access_request",
            entity_id=str(request.id),
            after_data={
                "target_telegram_id": request.requester_telegram_id,
                "action": action.value,
            },
        )
        return formatting.escape(f"🚫 Bỏ qua lần này. {name} vẫn có thể xin lại sau 24 giờ.")

    # IGNORE_IN_GROUP
    row = await policy.set_mode(
        actor=owner,
        bot_id=request.bot_id,
        chat_id=request.telegram_chat_id,
        telegram_user_id=request.requester_telegram_id,
        mode=GroupPolicyMode.IGNORE,
        reason="ignored_from_access_request",
    )
    await audit.record_action(
        request_id=request_id,
        actor=owner,
        action=AuditAction.GROUP_POLICY_SET.value,
        result=AuditResult.SUCCESS,
        entity_type="group_member_policy",
        entity_id=str(row.id),
        after_data={
            "target_telegram_id": request.requester_telegram_id,
            "chat_id": request.telegram_chat_id,
            "mode": GroupPolicyMode.IGNORE.value,
        },
    )
    return formatting.escape(
        f"🔕 MeoBot sẽ im lặng với {name} trong group này. Các group khác không đổi."
    )


#: Which owner decisions authorise the held question, and how.
_REPLAY_MODES: dict[AccessAction, AuthorizationMode] = {
    AccessAction.ANSWER_ONCE: AuthorizationMode.ANSWER_ONCE,
    AccessAction.GRANT_GUEST: AuthorizationMode.GUEST_WINDOW,
    AccessAction.CONFIRM_MEMBER: AuthorizationMode.MEMBER,
}


async def _settle_deferred_question(
    session: AsyncSession,
    settings: Settings,
    *,
    request: PendingGuestAccessRequest,
    action: AccessAction,
) -> uuid.UUID | None:
    """Authorise or purge the question that produced this approval card.

    Returns the id to process, or ``None`` when this decision does not produce
    an answer - being ignored is not something a stranger is told about, and
    telling them would turn a silent decision into a conversation the owner
    chose not to have.
    """
    service = DeferredGuestMessageService(session, settings)
    mode = _REPLAY_MODES.get(action)
    if mode is None:
        # IGNORE_ONCE / IGNORE_IN_GROUP. The stranger never became a user, so
        # what they wrote is purged rather than kept.
        await service.reject(pending_access_request_id=request.id)
        return None

    authorized = await service.authorize(pending_access_request_id=request.id, mode=mode)
    return authorized.id if authorized is not None else None


def enqueue_deferred_guest_reply(deferred_message_id: uuid.UUID) -> None:
    """Hand the held question to a worker. Never blocks the owner's button.

    A failure to enqueue is logged rather than raised: the authorization is
    already committed, and the sweep that retries stranded work will find it.
    """
    from meobot.tasks.guest_replay import process_deferred_guest_message

    try:
        process_deferred_guest_message.delay(str(deferred_message_id))
    except Exception:
        logger.warning(
            "deferred_guest_reply_enqueue_failed",
            extra={"deferred_message_id": str(deferred_message_id)},
        )


# --- Member quota requests --------------------------------------------------
@router.callback_query(F.data == QUOTA_REQUEST_CALLBACK)
async def handle_quota_request(
    query: CallbackQuery,
    database: Database,
    settings: Settings,
    request_id: uuid.UUID,
    actor: Actor | None = None,
) -> None:
    """A member asking for more chat quota today.

    At most one open request per member per quota date, so holding the button
    down produces one notification rather than a stream of them.
    """
    await query.answer()
    if actor is None or actor.user_id is None or query.from_user is None:
        return

    from meobot.application.quota_request_service import QuotaRequestService

    async with database.transaction() as session:
        service = QuotaRequestService(session, settings)
        request, created = await service.open_or_reuse(
            user_id=actor.user_id,
            telegram_user_id=query.from_user.id,
            display_name=actor.full_name,
            chat_id=query.message.chat.id if isinstance(query.message, Message) else 0,
            source_message_id=(
                query.message.message_id if isinstance(query.message, Message) else 0
            ),
        )
        if created:
            # The request and the owner's copy of it commit together. The
            # direct send this replaced could fail after the request was
            # stored, leaving a member waiting on a decision nobody had been
            # asked to make.
            await queue_quota_request_notification(session, settings, request=request)

    await formatting.answer_callback(
        query,
        formatting.escape(
            "Mình đã gửi yêu cầu tới Trưởng phòng. Bạn chờ phản hồi nhé."
            if created
            else "Yêu cầu của bạn đang chờ Trưởng phòng duyệt."
        ),
    )


@router.callback_query(F.data.startswith(f"{QUOTA_KIND}|"))
async def handle_quota_decision(
    query: CallbackQuery,
    database: Database,
    settings: Settings,
    request_id: uuid.UUID,
    actor: Actor | None = None,
) -> None:
    """Apply the owner's decision about one member's quota request."""
    await query.answer()
    if not _is_owner(query, settings, actor) or actor is None:
        await query.answer(NOT_OWNER, show_alert=True)
        return

    from meobot.application.quota_request_service import QuotaRequestService

    data = query.data or ""
    peeked = peek_request_id(data)
    if peeked is None:
        await formatting.edit_callback(query, formatting.escape(STALE_BUTTON))
        return
    _, quota_request_id = peeked

    async with database.transaction() as session:
        service = QuotaRequestService(session, settings)
        request = await service.by_id(quota_request_id)
        if request is None:
            await formatting.edit_callback(query, formatting.escape(STALE_BUTTON))
            return

        payload = parse_access_callback(
            data,
            secret=settings.callback_secret,
            binding=CallbackBinding(
                owner_telegram_id=query.from_user.id,
                subject_telegram_id=request.requester_telegram_id,
                telegram_chat_id=request.telegram_chat_id,
                source_message_id=request.source_message_id,
            ),
        )
        if payload is None or payload.is_expired(utcnow()):
            await formatting.edit_callback(query, formatting.escape(STALE_BUTTON))
            return
        action = payload.action
        if not isinstance(action, QuotaAction):  # pragma: no cover - kind-checked
            return
        if request.status is not PendingRequestStatus.OPEN:
            await formatting.edit_callback(query, formatting.escape(ALREADY_DECIDED))
            return

        quota = QuotaService(session, settings)
        audit = AuditService(session)
        before = await quota.inspect_member(user_id=request.user_id, role=Role.EMPLOYEE)

        if action is QuotaAction.ADD_10_TODAY:
            after = await quota.add_bonus(user_id=request.user_id, amount=QUOTA_BONUS_STEP)
            summary = f"➕ Đã thêm {QUOTA_BONUS_STEP} lượt cho hôm nay."
        elif action is QuotaAction.RESET_TODAY:
            after = await quota.reset_today(user_id=request.user_id)
            summary = "♻️ Đã đặt lại lượt hôm nay."
        elif action is QuotaAction.SET_CUSTOM_LIMIT:
            after = await quota.set_daily_limit(
                user_id=request.user_id,
                limit=CUSTOM_DAILY_LIMIT,
                actor_user_id=actor.user_id,
                actor_telegram_id=actor.telegram_user_id,
                reason="quota_request",
            )
            summary = f"⚙️ Hạn mức mới: {CUSTOM_DAILY_LIMIT} lượt/ngày."
        else:
            after = before
            summary = "🚫 Đã từ chối yêu cầu."

        await service.resolve(request, actor=actor, action=action)
        # The member is told privately, through the outbox, in this same
        # transaction - so the decision and the notice of it cannot diverge.
        await queue_quota_decision_notification(
            session, settings, request=request, action=action.value, outcome=summary
        )
        await audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.QUOTA_OVERRIDDEN.value,
            result=AuditResult.SUCCESS if action is not QuotaAction.DENY else AuditResult.DENIED,
            entity_type="quota_request",
            entity_id=str(request.id),
            before_data={"limit": before.limit, "used": before.used},
            after_data={
                "limit": after.limit,
                "used": after.used,
                "action": action.value,
                "target_user_id": str(request.user_id),
            },
        )

    await formatting.edit_callback(query, formatting.escape(summary))
