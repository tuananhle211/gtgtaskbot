"""Privacy, routing and the transactional outbox.

The claim this release makes is narrow and testable: **a personal reason cannot
reach a group, and a Telegram failure cannot lose a business action.** Most of
what follows is one of those two.

Nothing here touches Telegram. The delivery service takes an injected
:class:`~meobot.integrations.telegram.notifier.Notifier`, and every test passes
a recorder or a deliberately broken one.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from meobot.application.announcement_service import AnnouncementService, default_purpose_for
from meobot.application.audit_service import AuditService
from meobot.application.chat_registry_service import ChatRegistryService
from meobot.application.delivery_service import (
    DeliveryFailureMapper,
    TelegramDeliveryService,
)
from meobot.application.hr_notifications import HrNotificationService, summarise
from meobot.application.hr_request_service import HrRequestService
from meobot.application.notification_router import (
    NotificationRouter,
    RouteRequest,
    announcement_key,
    hr_approved_key,
    hr_submitted_key,
)
from meobot.application.outbox_service import OutboxService
from meobot.application.recipient_resolver import RecipientResolver
from meobot.core.config import Settings
from meobot.core.errors import AuthorizationError
from meobot.core.time import utcnow
from meobot.db.models.audit_log import AuditLog
from meobot.db.models.hr import HrRequest
from meobot.db.models.notifications import (
    Announcement,
    DeliveryAttempt,
    OutboundMessage,
    TelegramChat,
)
from meobot.db.models.user import User
from meobot.domain.access.models import UserStatus
from meobot.domain.hr.models import HrRequestType
from meobot.domain.hr.schedule import DEFAULT_SCHEDULE
from meobot.domain.identity.models import Actor, Role
from meobot.domain.notifications.models import (
    AnnouncementStatus,
    ChatPurpose,
    FailureCategory,
    NotificationEvent,
    OutboxStatus,
    PrivacyClassification,
    RecipientType,
    status_label,
)
from meobot.domain.notifications.routing import may_route
from meobot.domain.notifications.templates import (
    TEMPLATES,
    group_safe_templates,
    render,
)
from meobot.integrations.telegram.notifier import FakeNotifier

BOT = 123456
GROUP = -900_100
OWNER_TG = 777000111
MEMBER_TG = 970_001
TOMORROW = date(2026, 7, 31)
#: The HR service refuses a day that has passed by the real Ho Chi Minh City
#: clock, so the requests filed here use a moving day. The fixed ``TOMORROW``
#: stays for payloads that are typed in by hand.
NEXT_DAY = datetime.now(tz=ZoneInfo("Asia/Ho_Chi_Minh")).date() + timedelta(days=1)


@pytest.fixture
def owner() -> Actor:
    return Actor(
        user_id=uuid.uuid4(), telegram_user_id=OWNER_TG, full_name="Trưởng phòng", role=Role.OWNER
    )


async def make_user(
    session, *, telegram_id: int = MEMBER_TG, name: str = "Nguyễn Thị Linh", private: bool = True
) -> User:
    user = User(
        telegram_user_id=telegram_id,
        telegram_username="linh",
        full_name=name,
        role=Role.EMPLOYEE,
        active=True,
        status=UserStatus.ACTIVE,
        telegram_private_chat_id=telegram_id if private else None,
        private_chat_available=private,
    )
    session.add(user)
    await session.flush()
    return user


async def register_group(
    session,
    owner: Actor,
    *,
    purpose: ChatPurpose = ChatPurpose.DEPARTMENT_ANNOUNCEMENTS,
    chat_id: int = GROUP,
    name: str | None = None,
) -> TelegramChat:
    return await ChatRegistryService(session).register(
        actor=owner,
        bot_identity=BOT,
        telegram_chat_id=chat_id,
        chat_type="supergroup",
        telegram_title="Nhóm thử",
        display_name=name or purpose.value,
        purpose=purpose,
    )


# --- Privacy ----------------------------------------------------------------
@pytest.mark.parametrize(
    "classification",
    [PrivacyClassification.PERSONAL_PRIVATE, PrivacyClassification.SECRET],
)
def test_private_content_can_never_reach_a_group(
    classification: PrivacyClassification,
) -> None:
    """The single most important rule in the release."""
    verdict = may_route(classification=classification, recipient_type=RecipientType.REGISTERED_CHAT)
    assert verdict.allowed is False
    assert verdict.message


def test_management_only_is_also_kept_out_of_groups() -> None:
    """A management group is still a group."""
    assert not PrivacyClassification.MANAGEMENT_ONLY.may_reach_a_group


def test_a_secret_reaches_no_destination_at_all() -> None:
    for recipient in RecipientType:
        assert (
            may_route(classification=PrivacyClassification.SECRET, recipient_type=recipient).allowed
            is False
        )


def test_private_content_may_reach_the_person_it_is_about() -> None:
    assert may_route(
        classification=PrivacyClassification.PERSONAL_PRIVATE,
        recipient_type=RecipientType.USER_PRIVATE,
    ).allowed


def test_no_group_bound_template_can_carry_a_reason() -> None:
    """Enforcement by construction: the field does not exist to be filled."""
    for template in group_safe_templates():
        declared = set(template.required) | set(template.optional)
        for leaky in ("reason", "private_note", "decision_reason", "note"):
            assert leaky not in declared, f"{template.key} could carry {leaky}"


def test_a_template_rejects_a_field_it_never_declared() -> None:
    """Passing a reason to the neutral template is an error, not a leak."""
    with pytest.raises(ValueError, match="undeclared"):
        render("hr.attendance_leave", {"person": "Linh", "period": "nghỉ sáng", "reason": "ốm"})


def test_every_template_declares_a_classification() -> None:
    for template in TEMPLATES.values():
        assert isinstance(template.classification, PrivacyClassification)


async def test_the_router_refuses_a_private_payload_before_writing_a_row(
    session, owner: Actor, settings: Settings
) -> None:
    """Refusal happens *before* the outbox row exists, not at delivery time."""
    destination = await register_group(session, owner)
    result = await NotificationRouter(session, settings).route(
        [
            RouteRequest(
                event_type=NotificationEvent.HR_REQUEST_SUBMITTED,
                template_key="hr.request_to_approver",
                payload={
                    "heading": "📩 YÊU CẦU NGHỈ PHÉP",
                    "requester_name": "Linh",
                    "period": "Sáng ngày 31/07/2026",
                    "submitted_at": "15:42 ngày 30/07/2026",
                    "reason": "Chuyện riêng tư",
                },
                idempotency_key="privacy-test",
                aggregate_type="hr_request",
                destination=destination,
            )
        ]
    )

    assert result.queued == []
    assert result.refusals
    rows = (await session.execute(select(OutboundMessage))).scalars().all()
    assert rows == [], "a refused message must never become a durable row"


# --- Chat registry ----------------------------------------------------------
async def test_registering_a_group_is_idempotent(session, owner: Actor) -> None:
    first = await register_group(session, owner)
    second = await register_group(session, owner)

    rows = (await session.execute(select(TelegramChat))).scalars().all()
    assert len(rows) == 1
    assert first.id == second.id
    assert second.version == 2


async def test_a_member_cannot_register_a_group(session) -> None:
    member = Actor(user_id=uuid.uuid4(), telegram_user_id=5, full_name="M", role=Role.EMPLOYEE)
    with pytest.raises(AuthorizationError):
        await register_group(session, member)


async def test_a_private_chat_cannot_pretend_to_be_a_group(session, owner: Actor) -> None:
    """The shape an out-of-band destination registration would take."""
    from meobot.core.errors import ValidationError

    with pytest.raises(ValidationError):
        await ChatRegistryService(session).register(
            actor=owner,
            bot_identity=BOT,
            telegram_chat_id=OWNER_TG,
            chat_type="private",
            telegram_title=None,
            display_name="Giả vờ là group",
            purpose=ChatPurpose.DEPARTMENT_ANNOUNCEMENTS,
        )


async def test_a_renamed_group_keeps_its_registration(session, owner: Actor) -> None:
    """Identity is the numeric id; a title change must not break routing."""
    row = await register_group(session, owner)
    await ChatRegistryService(session).refresh_title(row, "Tên hoàn toàn mới")

    resolution = await RecipientResolver(
        session, __import__("meobot.core.config", fromlist=["get_settings"]).get_settings()
    ).resolve_chat(bot_identity=BOT, purpose=ChatPurpose.DEPARTMENT_ANNOUNCEMENTS)
    assert resolution.is_resolved
    assert resolution.chat is not None
    assert resolution.chat.telegram_chat_id == GROUP


async def test_a_disabled_destination_is_not_selected(
    session, owner: Actor, settings: Settings
) -> None:
    row = await register_group(session, owner)
    await ChatRegistryService(session).set_active(actor=owner, chat_id=row.id, active=False)

    resolution = await RecipientResolver(session, settings).resolve_chat(
        bot_identity=BOT, purpose=ChatPurpose.DEPARTMENT_ANNOUNCEMENTS
    )
    assert not resolution.is_resolved


async def test_a_bot_removed_from_a_group_stops_it_being_offered(
    session, owner: Actor, settings: Settings
) -> None:
    row = await register_group(session, owner)
    await ChatRegistryService(session).record_health(
        chat_id=row.id, category=FailureCategory.BOT_NOT_IN_CHAT
    )
    resolution = await RecipientResolver(session, settings).resolve_chat(
        bot_identity=BOT, purpose=ChatPurpose.DEPARTMENT_ANNOUNCEMENTS
    )
    assert not resolution.is_resolved


# --- Recipient resolution ---------------------------------------------------
async def test_a_natural_alias_resolves(session, owner: Actor, settings: Settings) -> None:
    await register_group(session, owner, purpose=ChatPurpose.CONTENT_TEAM, name="Team Nội dung")
    resolution = await RecipientResolver(session, settings).resolve_chat(
        bot_identity=BOT, text="Gửi thông báo này vào group Team Nội dung"
    )
    assert resolution.is_resolved


async def test_a_purpose_phrase_resolves_without_accents(
    session, owner: Actor, settings: Settings
) -> None:
    await register_group(session, owner)
    resolution = await RecipientResolver(session, settings).resolve_chat(
        bot_identity=BOT, text="thong bao cho toan phong 3 gio chieu hop"
    )
    assert resolution.is_resolved
    assert resolution.chat is not None
    assert resolution.chat.purpose is ChatPurpose.DEPARTMENT_ANNOUNCEMENTS


async def test_two_plausible_groups_ask_rather_than_guess(
    session, owner: Actor, settings: Settings
) -> None:
    """No similarity guess: MeoBot asks one question instead."""
    await register_group(
        session, owner, purpose=ChatPurpose.CONTENT_TEAM, chat_id=-1, name="Content Bác Tiến"
    )
    await register_group(
        session, owner, purpose=ChatPurpose.CONTENT_TEAM, chat_id=-2, name="Content Apexmed"
    )
    resolution = await RecipientResolver(session, settings).resolve_chat(
        bot_identity=BOT, text="gui vao group content"
    )
    assert resolution.is_ambiguous
    assert len(resolution.candidates) == 2


async def test_nothing_registered_resolves_to_nothing(session, settings: Settings) -> None:
    resolution = await RecipientResolver(session, settings).resolve_chat(
        bot_identity=BOT, text="thong bao cho toan phong"
    )
    assert not resolution.is_resolved


async def test_a_person_who_never_started_the_bot_is_unreachable(
    session, settings: Settings
) -> None:
    user = await make_user(session, private=False)
    resolution = await RecipientResolver(session, settings).private_destination(user_id=user.id)
    assert not resolution.is_resolved
    assert "chưa bắt đầu cuộc trò chuyện" in resolution.message
    assert "Linh" in resolution.message


async def test_a_username_is_never_used_as_a_destination(session, settings: Settings) -> None:
    """A username is not a chat id, and Telegram will not accept one."""
    user = await make_user(session, private=False)
    user.telegram_username = "linh"
    resolution = await RecipientResolver(session, settings).private_destination(user_id=user.id)
    assert resolution.telegram_chat_id is None


# --- Announcements ----------------------------------------------------------
async def test_only_the_owner_may_broadcast(session, settings: Settings) -> None:
    for role in (Role.EMPLOYEE, Role.TEAM_LEAD, Role.ADMIN):
        actor = Actor(user_id=uuid.uuid4(), telegram_user_id=1, full_name="X", role=role)
        assert AnnouncementService.may_broadcast(actor) is False
    owner = Actor(user_id=uuid.uuid4(), telegram_user_id=2, full_name="O", role=Role.OWNER)
    assert AnnouncementService.may_broadcast(owner) is True


async def test_publishing_creates_one_announcement_and_one_outbox_row(
    session, owner: Actor, settings: Settings, request_id: uuid.UUID
) -> None:
    destination = await register_group(session, owner)
    service = AnnouncementService(session, settings, AuditService(session))
    draft = await service.draft(
        actor=owner,
        content="Chiều nay 15:00 họp duyệt kế hoạch tháng.",
        source_chat_id=OWNER_TG,
        destination=destination,
    )

    _, result = await service.publish(actor=owner, request_id=request_id, announcement_id=draft.id)

    assert len(result.queued) == 1
    announcements = (await session.execute(select(Announcement))).scalars().all()
    assert len(announcements) == 1
    assert announcements[0].status is AnnouncementStatus.PUBLISHED
    outbound = (await session.execute(select(OutboundMessage))).scalars().all()
    assert len(outbound) == 1
    assert outbound[0].telegram_chat_id == GROUP


async def test_confirming_twice_publishes_once(
    session, owner: Actor, settings: Settings, request_id: uuid.UUID
) -> None:
    """A double tap on a slow connection must not tell the department twice."""
    destination = await register_group(session, owner)
    service = AnnouncementService(session, settings, AuditService(session))
    draft = await service.draft(
        actor=owner, content="Nội dung", source_chat_id=OWNER_TG, destination=destination
    )
    await service.publish(actor=owner, request_id=request_id, announcement_id=draft.id)
    await service.publish(actor=owner, request_id=request_id, announcement_id=draft.id)

    outbound = (await session.execute(select(OutboundMessage))).scalars().all()
    assert len(outbound) == 1


async def test_an_announcement_is_audited_with_source_and_destination(
    session, owner: Actor, settings: Settings, request_id: uuid.UUID
) -> None:
    destination = await register_group(session, owner)
    service = AnnouncementService(session, settings, AuditService(session))
    draft = await service.draft(
        actor=owner, content="Nội dung", source_chat_id=OWNER_TG, destination=destination
    )
    await service.publish(actor=owner, request_id=request_id, announcement_id=draft.id)

    entries = (await session.execute(select(AuditLog))).scalars().all()
    published = [row for row in entries if row.action == "announcement.published"]
    assert len(published) == 1
    payload = published[0].after_data
    assert payload is not None
    assert payload["source_chat_id"] == OWNER_TG
    assert payload["destination_chat_id"] == GROUP


def test_a_purpose_is_read_deterministically() -> None:
    assert default_purpose_for("đăng ký đây là group Content") is ChatPurpose.CONTENT_TEAM
    assert (
        default_purpose_for("đây là group thông báo toàn phòng")
        is ChatPurpose.DEPARTMENT_ANNOUNCEMENTS
    )
    assert default_purpose_for("dùng group này để cập nhật chấm công") is ChatPurpose.ATTENDANCE
    assert default_purpose_for("group gì đó") is None


def test_an_announcement_says_it_is_sent_on_somebody_s_behalf() -> None:
    """MeoBot must not impersonate the owner."""
    rendered = render("announcement.group", {"content": "Nội dung"})
    assert "gửi thay Trưởng phòng" in rendered


# --- HR routing -------------------------------------------------------------
async def hr_setup(session, owner: Actor, request_id: uuid.UUID) -> tuple[User, HrRequest]:
    user = await make_user(session)
    row = await HrRequestService(session, AuditService(session)).submit(
        actor=Actor(
            user_id=user.id,
            telegram_user_id=user.telegram_user_id,
            full_name=user.full_name,
            role=Role.EMPLOYEE,
        ),
        request_id=request_id,
        request_type=HrRequestType.MORNING_LEAVE,
        work_date=NEXT_DAY,
        schedule=DEFAULT_SCHEDULE,
        reason="Có việc gia đình",
    )
    return user, row


async def test_hr_submission_queues_a_private_card_with_the_reason(
    session, owner: Actor, settings: Settings, request_id: uuid.UUID
) -> None:
    user, row = await hr_setup(session, owner, request_id)
    result = await HrNotificationService(session, settings).on_submitted(
        row=row, requester=user, schedule=DEFAULT_SCHEDULE, source_chat_id=MEMBER_TG
    )

    assert len(result.queued) == 1
    message = result.queued[0]
    assert message.recipient_type is RecipientType.USER_PRIVATE
    assert message.telegram_chat_id == OWNER_TG
    assert message.privacy_classification is PrivacyClassification.PERSONAL_PRIVATE
    assert message.safe_payload_json["reason"] == "Có việc gia đình"


async def test_the_attendance_update_carries_no_reason(
    session, owner: Actor, settings: Settings, request_id: uuid.UUID
) -> None:
    """The whole point of two templates."""
    await register_group(session, owner, purpose=ChatPurpose.ATTENDANCE, chat_id=-777)
    _, row = await hr_setup(session, owner, request_id)
    await HrRequestService(session, AuditService(session)).approve(
        actor=owner, request_id=request_id, hr_request_id=row.id
    )

    result = await HrNotificationService(session, settings).on_decided(
        row=row,
        schedule=DEFAULT_SCHEDULE,
        bot_identity=BOT,
        approved=True,
        source_chat_id=OWNER_TG,
    )

    group_messages = [
        item for item in result.queued if item.recipient_type is RecipientType.REGISTERED_CHAT
    ]
    assert len(group_messages) == 1
    payload = group_messages[0].safe_payload_json
    assert "reason" not in payload
    assert "Có việc gia đình" not in str(payload)
    rendered = render(group_messages[0].template_key, dict(payload))
    assert "Có việc gia đình" not in rendered
    assert "Linh" in rendered


async def test_approval_queues_a_private_result_for_the_member(
    session, owner: Actor, settings: Settings, request_id: uuid.UUID
) -> None:
    _, row = await hr_setup(session, owner, request_id)
    await HrRequestService(session, AuditService(session)).approve(
        actor=owner, request_id=request_id, hr_request_id=row.id
    )
    result = await HrNotificationService(session, settings).on_decided(
        row=row,
        schedule=DEFAULT_SCHEDULE,
        bot_identity=BOT,
        approved=True,
        source_chat_id=OWNER_TG,
    )

    private = [item for item in result.queued if item.recipient_type is RecipientType.USER_PRIVATE]
    assert len(private) == 1
    assert private[0].telegram_chat_id == MEMBER_TG


async def test_a_rejection_reason_never_reaches_a_group(
    session, owner: Actor, settings: Settings, request_id: uuid.UUID
) -> None:
    await register_group(session, owner, purpose=ChatPurpose.ATTENDANCE, chat_id=-777)
    _, row = await hr_setup(session, owner, request_id)
    await HrRequestService(session, AuditService(session)).reject(
        actor=owner, request_id=request_id, hr_request_id=row.id, reason="Trùng lịch họp"
    )
    result = await HrNotificationService(session, settings).on_decided(
        row=row,
        schedule=DEFAULT_SCHEDULE,
        bot_identity=BOT,
        approved=False,
        source_chat_id=OWNER_TG,
    )

    for message in result.queued:
        assert message.recipient_type is RecipientType.USER_PRIVATE
    assert all(
        "Trùng lịch họp" not in str(item.safe_payload_json)
        for item in result.queued
        if item.recipient_type is RecipientType.REGISTERED_CHAT
    )


async def test_an_unreachable_member_does_not_invalidate_the_decision(
    session, owner: Actor, settings: Settings, request_id: uuid.UUID
) -> None:
    """The HR decision is the business fact; delivery is a separate problem."""
    user = await make_user(session, private=False)
    row = await HrRequestService(session, AuditService(session)).submit(
        actor=Actor(
            user_id=user.id,
            telegram_user_id=user.telegram_user_id,
            full_name=user.full_name,
            role=Role.EMPLOYEE,
        ),
        request_id=request_id,
        request_type=HrRequestType.FULL_DAY_LEAVE,
        work_date=NEXT_DAY,
        schedule=DEFAULT_SCHEDULE,
    )
    await HrRequestService(session, AuditService(session)).approve(
        actor=owner, request_id=request_id, hr_request_id=row.id
    )
    result = await HrNotificationService(session, settings).on_decided(
        row=row,
        schedule=DEFAULT_SCHEDULE,
        bot_identity=BOT,
        approved=True,
        source_chat_id=OWNER_TG,
    )

    assert result.queued == []
    stored = (await session.execute(select(HrRequest))).scalars().all()
    assert stored[0].status.value == "APPROVED"


async def test_a_missing_attendance_group_does_not_block_the_member_message(
    session, owner: Actor, settings: Settings, request_id: uuid.UUID
) -> None:
    _, row = await hr_setup(session, owner, request_id)
    await HrRequestService(session, AuditService(session)).approve(
        actor=owner, request_id=request_id, hr_request_id=row.id
    )
    result = await HrNotificationService(session, settings).on_decided(
        row=row,
        schedule=DEFAULT_SCHEDULE,
        bot_identity=BOT,
        approved=True,
        source_chat_id=OWNER_TG,
    )
    assert len(result.queued) == 1
    assert result.queued[0].recipient_type is RecipientType.USER_PRIVATE


def test_the_member_summary_carries_no_reason() -> None:
    row = HrRequest(
        requester_user_id=uuid.uuid4(),
        request_type=HrRequestType.MORNING_LEAVE,
        work_date=NEXT_DAY,
        reason="Chuyện rất riêng tư",
    )
    assert "riêng tư" not in summarise(row)


# --- Idempotency keys -------------------------------------------------------
def test_keys_are_stable_and_version_bound() -> None:
    request = uuid.uuid4()
    user = uuid.uuid4()
    assert hr_submitted_key(request, 1, 5) == hr_submitted_key(request, 1, 5)
    assert hr_submitted_key(request, 1, 5) != hr_submitted_key(request, 2, 5)
    assert hr_approved_key(request, 1, user) != hr_approved_key(request, 2, user)
    assert announcement_key(request, 1, 1) != announcement_key(request, 2, 1)


async def test_the_same_key_queues_once(session, settings: Settings, owner: Actor) -> None:
    destination = await register_group(session, owner)
    router = NotificationRouter(session, settings)
    request = RouteRequest(
        event_type=NotificationEvent.ANNOUNCEMENT_PUBLISHED,
        template_key="announcement.group",
        payload={"content": "Xin chào"},
        idempotency_key="same-key",
        aggregate_type="announcement",
        destination=destination,
    )
    first = await router.route([request])
    second = await router.route([request])

    assert len(first.queued) == 1
    assert second.queued == []
    assert len(second.duplicates) == 1
    rows = (await session.execute(select(OutboundMessage))).scalars().all()
    assert len(rows) == 1


# --- The outbox -------------------------------------------------------------
async def queue_one(
    session, settings: Settings, owner: Actor, *, key: str = "k1"
) -> OutboundMessage:
    destination = await register_group(session, owner)
    result = await NotificationRouter(session, settings).route(
        [
            RouteRequest(
                event_type=NotificationEvent.ANNOUNCEMENT_PUBLISHED,
                template_key="announcement.group",
                payload={"content": "Nội dung"},
                idempotency_key=key,
                aggregate_type="announcement",
                destination=destination,
            )
        ]
    )
    return result.queued[0]


async def test_a_claimed_message_leaves_the_queue(
    session, owner: Actor, settings: Settings
) -> None:
    """One worker's claim is what stops another taking the same row."""
    await queue_one(session, settings, owner)
    outbox = OutboxService(session, settings)

    first = await outbox.claim_batch()
    second = await outbox.claim_batch()

    assert len(first) == 1
    assert second == [], "a claimed message must not be claimable again"
    assert first[0].status is OutboxStatus.PROCESSING
    assert first[0].attempt_count == 1


async def test_a_temporary_failure_waits_and_retries(
    session, owner: Actor, settings: Settings
) -> None:
    row = await queue_one(session, settings, owner)
    outbox = OutboxService(session, settings)
    await outbox.claim_batch()

    status = await outbox.mark_failed(row, category=FailureCategory.NETWORK)

    assert status is OutboxStatus.RETRY_WAIT
    assert row.available_at > utcnow()


async def test_an_unreachable_recipient_stops_retrying(
    session, owner: Actor, settings: Settings
) -> None:
    """Retrying cannot fix somebody who has not started the bot."""
    row = await queue_one(session, settings, owner)
    outbox = OutboxService(session, settings)
    await outbox.claim_batch()

    status = await outbox.mark_failed(row, category=FailureCategory.PRIVATE_CHAT_UNAVAILABLE)
    assert status is OutboxStatus.PERMANENT_FAILURE


async def test_retries_are_bounded(session, owner: Actor, settings: Settings) -> None:
    row = await queue_one(session, settings, owner)
    outbox = OutboxService(session, settings)
    for _ in range(settings.notification_max_attempts + 1):
        row.status = OutboxStatus.PENDING
        row.available_at = utcnow() - timedelta(seconds=1)
        # The test session runs with autoflush off, so the reset has to be
        # pushed before the claim query can see it.
        await session.flush()
        await outbox.claim_batch()
        status = await outbox.mark_failed(row, category=FailureCategory.NETWORK)
    assert status is OutboxStatus.PERMANENT_FAILURE


def test_telegram_retry_after_is_respected(settings: Settings) -> None:
    """Arguing with a rate limiter makes the limit longer."""
    outbox = OutboxService(None, settings)  # type: ignore[arg-type]
    assert outbox.backoff_seconds(1, retry_after=42) == 42


def test_backoff_grows_and_is_capped(settings: Settings) -> None:
    outbox = OutboxService(None, settings)  # type: ignore[arg-type]
    early = outbox.backoff_seconds(1)
    late = outbox.backoff_seconds(6)
    assert early < late
    assert late <= settings.notification_retry_max_seconds


async def test_a_stale_claim_is_recovered(session, owner: Actor, settings: Settings) -> None:
    """A worker that died mid-delivery must not strand a message forever."""
    row = await queue_one(session, settings, owner)
    outbox = OutboxService(session, settings)
    await outbox.claim_batch()
    row.processing_started_at = utcnow() - timedelta(
        seconds=settings.notification_processing_timeout_seconds + 60
    )
    await session.flush()

    recovered = await outbox.recover_stale()

    assert recovered == 1
    assert row.status is OutboxStatus.RETRY_WAIT


async def test_a_delivered_message_is_terminal(session, owner: Actor, settings: Settings) -> None:
    row = await queue_one(session, settings, owner)
    outbox = OutboxService(session, settings)
    await outbox.claim_batch()
    await outbox.mark_delivered(row, telegram_message_id=99)

    assert row.status is OutboxStatus.DELIVERED
    assert row.status.is_settled
    assert await outbox.claim_batch() == []


async def test_attempt_history_is_recorded(session, owner: Actor, settings: Settings) -> None:
    row = await queue_one(session, settings, owner)
    outbox = OutboxService(session, settings)
    await outbox.claim_batch()
    await outbox.mark_failed(row, category=FailureCategory.NETWORK)
    row.status = OutboxStatus.PENDING
    row.available_at = utcnow() - timedelta(seconds=1)
    await session.flush()
    await outbox.claim_batch()
    await outbox.mark_delivered(row)

    attempts = await outbox.attempts(row.id)
    assert [item.attempt_number for item in attempts] == [1, 2]
    assert attempts[0].result.value == "TEMPORARY_FAILURE"
    assert attempts[1].result.value == "DELIVERED"


async def test_no_attempt_stores_a_provider_response_body(
    session, owner: Actor, settings: Settings
) -> None:
    row = await queue_one(session, settings, owner)
    outbox = OutboxService(session, settings)
    await outbox.claim_batch()
    await outbox.mark_failed(row, category=FailureCategory.NETWORK)

    attempts = (await session.execute(select(DeliveryAttempt))).scalars().all()
    for attempt in attempts:
        assert not hasattr(attempt, "provider_body")
        assert attempt.error_category is FailureCategory.NETWORK


# --- Failure categorisation -------------------------------------------------
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Forbidden: bot was blocked by the user", FailureCategory.PRIVATE_CHAT_UNAVAILABLE),
        ("Bad Request: chat not found", FailureCategory.CHAT_NOT_FOUND),
        ("Forbidden: bot is not a member of the group chat", FailureCategory.BOT_NOT_IN_CHAT),
        ("Bad Request: not enough rights to send text messages", FailureCategory.BOT_CANNOT_SEND),
        ("Too Many Requests: retry after 30", FailureCategory.RATE_LIMITED),
        ("Read timeout", FailureCategory.NETWORK),
        ("502 Bad Gateway", FailureCategory.PROVIDER_UNAVAILABLE),
        ("something nobody has seen before", FailureCategory.UNKNOWN),
    ],
)
def test_provider_failures_are_categorised(text: str, expected: FailureCategory) -> None:
    assert DeliveryFailureMapper.categorize(text) is expected


def test_retry_after_is_read_from_the_provider() -> None:
    assert DeliveryFailureMapper.retry_after("Too Many Requests: retry after 30") == 30
    assert DeliveryFailureMapper.retry_after("Read timeout") is None


# --- Delivery ---------------------------------------------------------------
async def test_delivery_renders_and_sends(session, owner: Actor, settings: Settings) -> None:
    row = await queue_one(session, settings, owner)
    notifier = FakeNotifier()

    result = await TelegramDeliveryService(notifier).deliver(row)

    assert result.delivered
    assert len(notifier.sent) == 1
    chat_id, text = notifier.sent[0]
    assert chat_id == GROUP
    assert "Nội dung" in text


async def test_a_provider_exception_becomes_a_category_not_a_crash(
    session, owner: Actor, settings: Settings
) -> None:
    row = await queue_one(session, settings, owner)

    class Broken:
        async def send(self, chat_id: int, text: str, **_: object) -> bool:
            raise RuntimeError("Forbidden: bot was blocked by the user")

    result = await TelegramDeliveryService(Broken()).deliver(row)  # type: ignore[arg-type]

    assert result.delivered is False
    assert result.category is FailureCategory.PRIVATE_CHAT_UNAVAILABLE


async def test_an_unrenderable_payload_is_refused_not_retried(
    session, owner: Actor, settings: Settings
) -> None:
    row = await queue_one(session, settings, owner)
    row.safe_payload_json = {}  # the template needs "content"
    await session.flush()

    result = await TelegramDeliveryService(FakeNotifier()).deliver(row)

    assert result.delivered is False
    assert result.category is FailureCategory.DESTINATION_REFUSED


# --- Vietnamese surface -----------------------------------------------------
def test_every_delivery_status_reads_as_vietnamese() -> None:
    for status in OutboxStatus:
        label = status_label(status)
        assert status.value not in label
        assert label


def test_a_template_key_is_never_shown_as_is() -> None:
    from meobot.bot.handlers.notifications import _event_label

    for event in NotificationEvent:
        label = _event_label(event.value)
        assert event.value not in label
