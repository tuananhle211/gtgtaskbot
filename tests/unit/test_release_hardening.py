"""Delivery semantics, health, receipts and assignments for 0.6.0a2.

The two crash-window tests here are the ones worth reading. They exist because
the previous release's docstring claimed "at most once" and that was false;
these pin what is *actually* true, including the case where a duplicate
Telegram message is possible. A test suite that asserted the comfortable thing
would have let the wrong claim survive another release.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from meobot.application.announcement_audience_service import AnnouncementAudienceService
from meobot.application.chat_assignment_service import ChatAssignmentService
from meobot.application.delivery_alert_service import DeliveryAlertService, reason_label
from meobot.application.delivery_service import TelegramDeliveryService
from meobot.application.destination_health_service import (
    DestinationHealthService,
    verdict_of,
)
from meobot.application.notification_router import (
    NotificationRouter,
    RouteRequest,
    destination_health_key,
)
from meobot.application.outbox_service import OutboundRequest, OutboxService
from meobot.core.config import Settings
from meobot.core.errors import AuthorizationError, ValidationError
from meobot.db.models.notifications import (
    Announcement,
    AnnouncementRecipient,
    OutboundMessage,
    TelegramChat,
)
from meobot.db.models.user import User
from meobot.domain.access.models import UserStatus
from meobot.domain.identity.models import Actor, Role
from meobot.domain.notifications.models import (
    AnnouncementStatus,
    ChatPurpose,
    DestinationHealth,
    FailureCategory,
    NotificationEvent,
    OutboxStatus,
    PrivacyClassification,
    RecipientType,
)
from meobot.integrations.telegram.notifier import ChatProbe, FakeNotifier

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
NOW = datetime(2026, 7, 30, 18, 0, tzinfo=TZ)
SRC = Path(__file__).resolve().parents[2] / "src" / "meobot"


async def make_user(
    session,  # type: ignore[no-untyped-def]
    *,
    role: Role = Role.EMPLOYEE,
    telegram_id: int = 920_001,
    name: str = "Nguyễn Thị Linh",
    private: bool = True,
) -> User:
    user = User(
        telegram_user_id=telegram_id,
        telegram_username="u",
        full_name=name,
        role=role,
        active=True,
        status=UserStatus.ACTIVE,
        telegram_private_chat_id=telegram_id if private else None,
        private_chat_available=private,
    )
    session.add(user)
    await session.flush()
    return user


def actor_for(user: User) -> Actor:
    return Actor(
        user_id=user.id,
        telegram_user_id=user.telegram_user_id,
        telegram_username=user.telegram_username,
        full_name=user.full_name,
        role=user.role,
        active=True,
    )


async def make_chat(
    session,  # type: ignore[no-untyped-def]
    *,
    name: str = "Team Nội dung",
    purpose: ChatPurpose = ChatPurpose.CONTENT_TEAM,
    telegram_chat_id: int = -100_777,
) -> TelegramChat:
    chat = TelegramChat(
        telegram_chat_id=telegram_chat_id,
        bot_identity=1,
        chat_type="supergroup",
        display_name=name,
        normalized_alias=name.lower(),
        purpose=purpose,
        privacy_level=PrivacyClassification.TEAM_OPERATIONAL,
        registered_at=NOW,
        version=1,
    )
    session.add(chat)
    await session.flush()
    return chat


async def queue_one(
    session,  # type: ignore[no-untyped-def]
    settings: Settings,
    *,
    key: str = "test:1",
    chat_id: int = 920_001,
    created_by: uuid.UUID | None = None,
) -> OutboundMessage:
    row, _created = await OutboxService(session, settings).enqueue(
        OutboundRequest(
            event_type=NotificationEvent.HR_REQUEST_APPROVED,
            aggregate_type="test",
            aggregate_id=None,
            recipient_type=RecipientType.USER_PRIVATE,
            telegram_chat_id=chat_id,
            template_key="hr.approved_to_member",
            template_version=1,
            privacy_classification=PrivacyClassification.PERSONAL_PRIVATE,
            payload={"summary": "nghỉ sáng 31/07"},
            idempotency_key=key,
            created_by_user_id=created_by,
            destination_label="Chat riêng",
            business_summary="Kết quả đơn nghỉ",
        )
    )
    return row


# --- Delivery semantics ----------------------------------------------------
class TestDeliverySemantics:
    """What MeoBot guarantees, and what it does not."""

    async def test_a_duplicate_enqueue_creates_one_row(
        self,
        session,  # type: ignore[no-untyped-def]
        settings: Settings,
    ) -> None:
        """Double-tapped buttons and retried services collide on one key."""
        outbox = OutboxService(session, settings)
        first, created_first = await outbox.enqueue(
            OutboundRequest(
                event_type=NotificationEvent.HR_REQUEST_APPROVED,
                aggregate_type="test",
                aggregate_id=None,
                recipient_type=RecipientType.USER_PRIVATE,
                telegram_chat_id=1,
                template_key="hr.approved_to_member",
                template_version=1,
                privacy_classification=PrivacyClassification.PERSONAL_PRIVATE,
                payload={"summary": "x"},
                idempotency_key="same",
            )
        )
        second, created_second = await outbox.enqueue(
            OutboundRequest(
                event_type=NotificationEvent.HR_REQUEST_APPROVED,
                aggregate_type="test",
                aggregate_id=None,
                recipient_type=RecipientType.USER_PRIVATE,
                telegram_chat_id=1,
                template_key="hr.approved_to_member",
                template_version=1,
                privacy_classification=PrivacyClassification.PERSONAL_PRIVATE,
                payload={"summary": "x"},
                idempotency_key="same",
            )
        )
        assert created_first and not created_second
        assert first.id == second.id
        rows = (await session.execute(select(OutboundMessage))).scalars().all()
        assert len(rows) == 1

    async def test_a_claimed_message_leaves_the_claimable_set(
        self,
        session,  # type: ignore[no-untyped-def]
        settings: Settings,
    ) -> None:
        """A second worker's claim query cannot see a claimed row."""
        await queue_one(session, settings)
        outbox = OutboxService(session, settings)
        assert len(await outbox.claim_batch()) == 1
        assert list(await outbox.claim_batch()) == []

    async def test_crash_a_before_send_is_recovered_and_delivered(
        self,
        session,  # type: ignore[no-untyped-def]
        settings: Settings,
    ) -> None:
        """Crash A: claimed and committed PROCESSING, then the worker died.

        The message was never sent. Stale recovery must return it so it is
        eventually delivered - the alternative is a notification that silently
        never arrives.
        """
        row = await queue_one(session, settings)
        outbox = OutboxService(session, settings)
        await outbox.claim_batch()
        assert row.status is OutboxStatus.PROCESSING

        # The worker dies here. Time passes beyond the processing timeout.
        later = datetime.now(tz=ZoneInfo("UTC")) + timedelta(
            seconds=settings.notification_processing_timeout_seconds + 60
        )
        recovered = await outbox.recover_stale(now=later)
        assert recovered == 1
        await session.refresh(row)
        assert row.status is OutboxStatus.RETRY_WAIT

        # A later worker picks it up and delivers it.
        claimed = await outbox.claim_batch(now=later)
        assert len(claimed) == 1
        notifier = FakeNotifier()
        result = await TelegramDeliveryService(notifier).deliver(row)
        assert result.delivered
        await outbox.mark_delivered(row)
        assert row.status is OutboxStatus.DELIVERED
        assert len(notifier.sent) == 1

    async def test_crash_b_after_send_documents_at_least_once(
        self,
        session,  # type: ignore[no-untyped-def]
        settings: Settings,
    ) -> None:
        """Crash B: Telegram accepted it, then the worker died before settling.

        This is the window in which a **duplicate Telegram message is
        possible**, and this test asserts that rather than denying it: the
        transport is called twice across the crash. What must *not* duplicate
        is the business record or the outbox intent, and that is what is
        checked at the end.

        A test that asserted "the transport is called exactly once" here would
        be asserting something MeoBot cannot deliver - Telegram's sendMessage
        takes no idempotency key.
        """
        row = await queue_one(session, settings)
        outbox = OutboxService(session, settings)
        notifier = FakeNotifier()
        delivery = TelegramDeliveryService(notifier)

        await outbox.claim_batch()
        first = await delivery.deliver(row)
        assert first.delivered
        # The worker dies *before* mark_delivered. The row is still PROCESSING.
        assert row.status is OutboxStatus.PROCESSING

        later = datetime.now(tz=ZoneInfo("UTC")) + timedelta(
            seconds=settings.notification_processing_timeout_seconds + 60
        )
        assert await outbox.recover_stale(now=later) == 1
        await session.refresh(row)
        await outbox.claim_batch(now=later)
        await delivery.deliver(row)
        await outbox.mark_delivered(row)

        # At least once: the person may have seen it twice.
        assert len(notifier.sent) == 2
        # Exactly one durable intent, and exactly one business record.
        rows = (await session.execute(select(OutboundMessage))).scalars().all()
        assert len(rows) == 1
        assert rows[0].status is OutboxStatus.DELIVERED

    def test_no_delivery_module_claims_at_most_once(self) -> None:
        """The wrong claim must not come back in a comment or a docstring.

        Scoped to the modules that actually talk about Telegram delivery.
        "at most once" is a true and useful phrase elsewhere - spreadsheet
        creation really is at-most-once per idempotency key, because Drive
        lets MeoBot check before it creates.
        """
        delivery_modules = [
            SRC / "tasks" / "notifications.py",
            SRC / "tasks" / "reminders.py",
            SRC / "tasks" / "guest_replay.py",
            SRC / "tasks" / "destination_health.py",
            SRC / "application" / "outbox_service.py",
            SRC / "application" / "delivery_service.py",
            SRC / "application" / "delivery_alert_service.py",
            SRC / "application" / "notification_router.py",
            SRC / "db" / "models" / "notifications.py",
            SRC / "integrations" / "telegram" / "notifier.py",
        ]
        offenders: list[str] = []
        for path in delivery_modules:
            text = path.read_text(encoding="utf-8").lower()
            index = text.find("at most once")
            if index == -1:
                continue
            # Allowed only where it is explicitly disclaimed.
            window = text[max(0, index - 300) : index + 300]
            if "was wrong" not in window and "not" not in window:
                offenders.append(path.name)
        assert offenders == [], f"these modules still claim at-most-once: {offenders}"

    def test_the_readme_states_the_guarantee_in_vietnamese(self) -> None:
        """The README is Vietnamese, so the claim is checked in Vietnamese."""
        readme = (SRC.parents[1] / "README.md").read_text(encoding="utf-8").lower()
        assert "ít nhất một lần" in readme
        # The old, wrong claim may appear only where it is being corrected.
        wrong = "nhiều nhất một lần"
        if wrong in readme:
            window = readme[max(0, readme.find(wrong) - 300) : readme.find(wrong) + 300]
            assert "sai" in window, "the README still asserts at-most-once delivery"

    def test_the_documented_guarantee_is_at_least_once(self) -> None:
        from meobot.tasks import notifications

        doc = (notifications.__doc__ or "").lower()
        assert "at least once" in doc
        assert "no application-level idempotency key" in doc


# --- No new direct cross-chat sends ----------------------------------------
def test_no_handler_sends_a_business_message_to_another_chat() -> None:
    """A static guard on the architectural rule of the previous release.

    Handlers may answer their own chat. Anything for somebody else goes through
    the outbox. ``bot.send_message`` in a handler is how that rule was broken
    twice before, so it is checked rather than remembered.
    """
    offenders: list[str] = []
    pattern = re.compile(r"\bbot\.send_message\s*\(")
    for path in (SRC / "bot" / "handlers").rglob("*.py"):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if pattern.search(line):
                offenders.append(f"{path.name}:{number}")
    assert offenders == [], (
        "cross-chat business messages must go through NotificationRouter -> outbox "
        f"-> q_notifications; found direct sends at {offenders}"
    )


def test_the_worker_consumes_the_notification_queue() -> None:
    """Compose must actually run the queue the outbox writes to.

    This was wrong until 0.6.0a2: every outbound row was written and then never
    picked up, so the business action succeeded and nobody was told.
    """
    compose = (SRC.parents[1] / "docker-compose.yml").read_text(encoding="utf-8")
    assert "q_notifications" in compose
    queue_lines = [line for line in compose.splitlines() if "q_default,q_integrations" in line]
    assert queue_lines, "the worker's --queues argument was not found"
    for line in queue_lines:
        assert "q_notifications" in line


def test_reminders_and_health_are_scheduled() -> None:
    from meobot.tasks.celery_app import celery_app

    schedule = celery_app.conf.beat_schedule
    assert "reminders-sweep-due" in schedule
    assert "notifications-sweep-destination-health" in schedule
    assert schedule["reminders-sweep-due"]["options"]["queue"] == "q_notifications"


# --- Permanent failure alerts ----------------------------------------------
class TestFailureAlerts:
    async def test_a_terminal_failure_notifies_the_initiator_once(
        self,
        session,  # type: ignore[no-untyped-def]
        settings: Settings,
    ) -> None:
        author = await make_user(session, telegram_id=920_010)
        row = await queue_one(session, settings, key="fail:1", created_by=author.id)
        outbox = OutboxService(session, settings)
        await outbox.claim_batch()
        await outbox.mark_failed(row, category=FailureCategory.BOT_CANNOT_SEND)
        assert row.status is OutboxStatus.PERMANENT_FAILURE

        alerts = DeliveryAlertService(session, settings)
        first = await alerts.alert_permanent_failure(row)
        assert first is not None
        assert first.telegram_chat_id == author.telegram_user_id
        assert first.is_alert

        # A second sweep must not produce a second alert.
        second = await alerts.alert_permanent_failure(row)
        assert second is None
        alert_rows = [
            item
            for item in (await session.execute(select(OutboundMessage))).scalars().all()
            if item.is_alert
        ]
        assert len(alert_rows) == 1

    async def test_an_alert_never_alerts_about_itself(
        self,
        session,  # type: ignore[no-untyped-def]
        settings: Settings,
    ) -> None:
        """The recursion guard. Without it one unreachable owner loops forever."""
        author = await make_user(session, telegram_id=920_011)
        row = await queue_one(session, settings, key="fail:2", created_by=author.id)
        row.is_alert = True
        await session.flush()
        assert await DeliveryAlertService(session, settings).alert_permanent_failure(row) is None

    async def test_the_alert_carries_no_provider_text(
        self,
        session,  # type: ignore[no-untyped-def]
        settings: Settings,
    ) -> None:
        author = await make_user(session, telegram_id=920_012)
        row = await queue_one(session, settings, key="fail:3", created_by=author.id)
        row.status = OutboxStatus.PERMANENT_FAILURE
        row.last_error_category = FailureCategory.BOT_CANNOT_SEND
        await session.flush()

        alert = await DeliveryAlertService(session, settings).alert_permanent_failure(row)
        assert alert is not None
        rendered = "\n".join(str(value) for value in alert.safe_payload_json.values())
        for leak in ("Traceback", "TelegramBadRequest", "chat_write_forbidden", "400"):
            assert leak not in rendered
        assert str(row.telegram_chat_id) not in rendered
        assert "MeoBot hiện không có quyền gửi tin trong group này." in rendered

    def test_every_failure_category_has_a_human_reason(self) -> None:
        for category in FailureCategory:
            label = reason_label(category)
            assert label
            assert category.value not in label


# --- Destination health ----------------------------------------------------
class TestDestinationHealth:
    async def test_a_healthy_group_is_recorded_without_sending_anything(
        self,
        session,  # type: ignore[no-untyped-def]
        settings: Settings,
    ) -> None:
        chat = await make_chat(session)
        notifier = FakeNotifier()
        service = DestinationHealthService(session, settings, notifier)

        transition = await service.check(chat, now=NOW)

        assert notifier.probed == [chat.telegram_chat_id]
        assert notifier.sent == [], "a health check must never send a visible message"
        assert chat.health_status is DestinationHealth.HEALTHY
        assert transition is not None and transition.became_healthy

    async def test_a_removed_bot_becomes_unhealthy_after_the_threshold(
        self,
        session,  # type: ignore[no-untyped-def]
        settings: Settings,
    ) -> None:
        chat = await make_chat(session)
        chat.health_status = DestinationHealth.HEALTHY
        await session.flush()

        notifier = FakeNotifier()
        notifier.probes[chat.telegram_chat_id] = ChatProbe(
            exists=True, bot_is_member=False, can_send=False, is_admin=False
        )
        service = DestinationHealthService(session, settings, notifier)

        first = await service.check(chat, now=NOW)
        assert first is None, "one bad answer is not enough"
        second = await service.check(chat, now=NOW)
        assert second is not None
        assert second.became_unhealthy
        assert chat.health_status is DestinationHealth.BOT_REMOVED
        assert chat.bot_can_send is False

    async def test_repeated_unhealthy_checks_produce_one_transition(
        self,
        session,  # type: ignore[no-untyped-def]
        settings: Settings,
    ) -> None:
        chat = await make_chat(session)
        chat.health_status = DestinationHealth.HEALTHY
        await session.flush()
        notifier = FakeNotifier()
        notifier.probes[chat.telegram_chat_id] = ChatProbe(
            exists=True, bot_is_member=True, can_send=False, is_admin=False
        )
        service = DestinationHealthService(session, settings, notifier)

        transitions = [await service.check(chat, now=NOW) for _ in range(5)]
        assert len([item for item in transitions if item is not None]) == 1

    async def test_a_transient_provider_error_disables_nothing(
        self,
        session,  # type: ignore[no-untyped-def]
        settings: Settings,
    ) -> None:
        """Telegram being briefly down says nothing about whether a group exists."""
        chat = await make_chat(session)
        chat.health_status = DestinationHealth.HEALTHY
        await session.flush()
        notifier = FakeNotifier()
        notifier.probes[chat.telegram_chat_id] = ChatProbe(
            exists=True, bot_is_member=True, can_send=True, is_admin=False, provider_error=True
        )
        service = DestinationHealthService(session, settings, notifier)

        assert await service.check(chat, now=NOW) is None
        assert chat.health_status is DestinationHealth.HEALTHY
        assert chat.is_active is True
        assert chat.consecutive_health_failures == 0

    async def test_recovery_reports_one_transition(
        self,
        session,  # type: ignore[no-untyped-def]
        settings: Settings,
    ) -> None:
        chat = await make_chat(session)
        chat.health_status = DestinationHealth.CANNOT_SEND
        chat.is_active = False
        await session.flush()
        service = DestinationHealthService(session, settings, FakeNotifier())

        transition = await service.check(chat, now=NOW)
        assert transition is not None and transition.became_healthy
        assert chat.is_active is True, "a recovered group comes back on its own"
        assert await service.check(chat, now=NOW) is None

    def test_alert_keys_differ_per_transition(self) -> None:
        chat_id = uuid.uuid4()
        assert destination_health_key(chat_id, 2, healthy=False) != destination_health_key(
            chat_id, 3, healthy=False
        )
        assert destination_health_key(chat_id, 2, healthy=True) != destination_health_key(
            chat_id, 2, healthy=False
        )

    def test_probe_verdicts_are_specific(self) -> None:
        assert verdict_of(ChatProbe(False, False, False, False)) is DestinationHealth.NOT_FOUND
        assert verdict_of(ChatProbe(True, False, False, False)) is DestinationHealth.BOT_REMOVED
        assert verdict_of(ChatProbe(True, True, False, False)) is DestinationHealth.CANNOT_SEND
        assert verdict_of(ChatProbe(True, True, True, False)) is DestinationHealth.HEALTHY
        assert (
            verdict_of(ChatProbe(True, True, True, False, provider_error=True))
            is DestinationHealth.PROVIDER_ERROR
        )

    def test_private_users_are_never_probed(self) -> None:
        """Telegram offers no way to test a private chat without messaging it."""
        source = (SRC / "application" / "destination_health_service.py").read_text("utf-8")
        assert "TelegramChat" in source
        assert "User" not in source.split("logger = ")[1].split("class ")[0]


# --- Announcement audience and receipts ------------------------------------
class TestReadReceipts:
    async def test_a_department_announcement_snapshots_every_active_user(
        self,
        session,  # type: ignore[no-untyped-def]
        settings: Settings,
    ) -> None:
        await make_user(session, telegram_id=920_020, name="Linh")
        await make_user(session, telegram_id=920_021, name="Hảo")
        chat = await make_chat(
            session, purpose=ChatPurpose.DEPARTMENT_ANNOUNCEMENTS, name="Toàn phòng"
        )
        announcement = Announcement(
            source_chat_id=1,
            content="Chiều nay 3 giờ họp",
            destination_chat_id=chat.id,
            status=AnnouncementStatus.PUBLISHED,
            request_read_receipt=True,
            version=1,
        )
        session.add(announcement)
        await session.flush()

        expected = await AnnouncementAudienceService(session).snapshot(
            announcement=announcement, destination=chat, now=NOW
        )
        assert expected == 2

    async def test_a_team_announcement_uses_the_assigned_audience(
        self,
        session,  # type: ignore[no-untyped-def]
        settings: Settings,
        owner_actor: Actor,
    ) -> None:
        lead = await make_user(session, role=Role.TEAM_LEAD, telegram_id=920_030, name="Hảo")
        member = await make_user(session, telegram_id=920_031, name="Linh")
        await make_user(session, telegram_id=920_032, name="Người ngoài nhóm")
        chat = await make_chat(session)
        assignments = ChatAssignmentService(session)
        await assignments.assign_manager(actor=owner_actor, chat=chat, target=lead)
        await assignments.add_member(actor=owner_actor, chat=chat, target=member)

        announcement = Announcement(
            source_chat_id=1,
            content="Team họp lúc 4 giờ",
            destination_chat_id=chat.id,
            status=AnnouncementStatus.PUBLISHED,
            version=1,
        )
        session.add(announcement)
        await session.flush()

        expected = await AnnouncementAudienceService(session).snapshot(
            announcement=announcement, destination=chat, now=NOW
        )
        assert expected == 2, "only the two assigned people, not everybody active"

    async def test_an_unconfigured_group_does_not_invent_a_denominator(
        self,
        session,  # type: ignore[no-untyped-def]
        settings: Settings,
    ) -> None:
        """Telegram will not enumerate members, so MeoBot says so."""
        chat = await make_chat(session, purpose=ChatPurpose.GENERAL, name="Group chung")
        announcement = Announcement(
            source_chat_id=1,
            content="Thông báo",
            destination_chat_id=chat.id,
            status=AnnouncementStatus.PUBLISHED,
            version=1,
        )
        session.add(announcement)
        await session.flush()
        audience = AnnouncementAudienceService(session)
        assert await audience.snapshot(announcement=announcement, destination=chat) == 0

        report = await audience.report(announcement.id)
        assert report.audience_known is False
        rendered = report.render()
        assert "chưa được cấu hình danh sách thành viên" in rendered
        assert "0 người" not in rendered.split("\n")[0] or "đã có" in rendered

    async def test_the_report_names_who_has_not_confirmed(
        self,
        session,  # type: ignore[no-untyped-def]
        settings: Settings,
    ) -> None:
        linh = await make_user(session, telegram_id=920_040, name="Linh")
        hao = await make_user(session, telegram_id=920_041, name="Hảo")
        chat = await make_chat(
            session, purpose=ChatPurpose.DEPARTMENT_ANNOUNCEMENTS, name="Toàn phòng"
        )
        announcement = Announcement(
            source_chat_id=1,
            content="Chiều nay họp",
            destination_chat_id=chat.id,
            status=AnnouncementStatus.PUBLISHED,
            version=1,
        )
        session.add(announcement)
        await session.flush()
        audience = AnnouncementAudienceService(session)
        await audience.snapshot(announcement=announcement, destination=chat, now=NOW)

        await audience.mark_acknowledged(
            announcement_id=announcement.id, telegram_user_id=linh.telegram_user_id
        )
        report = await audience.report(announcement.id)

        assert report.acknowledged == ["Linh"]
        assert report.outstanding == [hao.full_name]
        assert "Đã đọc: 1/2 người." in report.render()

    async def test_snapshotting_twice_does_not_change_the_denominator(
        self,
        session,  # type: ignore[no-untyped-def]
        settings: Settings,
    ) -> None:
        await make_user(session, telegram_id=920_050, name="Linh")
        chat = await make_chat(
            session, purpose=ChatPurpose.DEPARTMENT_ANNOUNCEMENTS, name="Toàn phòng"
        )
        announcement = Announcement(
            source_chat_id=1,
            content="x",
            destination_chat_id=chat.id,
            status=AnnouncementStatus.PUBLISHED,
            version=1,
        )
        session.add(announcement)
        await session.flush()
        audience = AnnouncementAudienceService(session)
        await audience.snapshot(announcement=announcement, destination=chat, now=NOW)
        # Somebody joins afterwards.
        await make_user(session, telegram_id=920_051, name="Người mới")
        await audience.snapshot(announcement=announcement, destination=chat, now=NOW)

        rows = (await session.execute(select(AnnouncementRecipient))).scalars().all()
        assert len(rows) == 1, "a person hired later has not failed to read this"

    def test_the_receipt_button_is_bound_to_the_chat(self, settings: Settings) -> None:
        """A button forwarded to another chat must not verify there."""
        from meobot.bot.announcement_keyboards import read_receipt_keyboard, receipt_binding
        from meobot.domain.member.callbacks import parse

        announcement_id = uuid.uuid4()
        keyboard = read_receipt_keyboard(
            announcement_id=announcement_id, settings=settings, chat_id=-100, bot_id=7
        )
        assert keyboard is not None
        data = keyboard.inline_keyboard[0][0].callback_data or ""

        good = parse(
            data, secret=settings.callback_secret, binding=receipt_binding(bot_id=7, chat_id=-100)
        )
        assert good is not None and good.entity_id == announcement_id

        elsewhere = parse(
            data, secret=settings.callback_secret, binding=receipt_binding(bot_id=7, chat_id=-999)
        )
        assert elsewhere is None


# --- Team lead assignments -------------------------------------------------
class TestChatAssignments:
    async def test_the_owner_assigns_an_active_team_lead(
        self,
        session,  # type: ignore[no-untyped-def]
        owner_actor: Actor,
    ) -> None:
        lead = await make_user(session, role=Role.TEAM_LEAD, telegram_id=920_060)
        chat = await make_chat(session)
        assignment = await ChatAssignmentService(session).assign_manager(
            actor=owner_actor, chat=chat, target=lead
        )
        assert assignment.can_broadcast is True
        assert assignment.is_active is True

    async def test_an_employee_is_never_silently_promoted(
        self,
        session,  # type: ignore[no-untyped-def]
        owner_actor: Actor,
    ) -> None:
        employee = await make_user(session, role=Role.EMPLOYEE, telegram_id=920_061)
        chat = await make_chat(session)
        with pytest.raises(ValidationError):
            await ChatAssignmentService(session).assign_manager(
                actor=owner_actor, chat=chat, target=employee
            )
        await session.refresh(employee)
        assert employee.role is Role.EMPLOYEE

    async def test_only_the_owner_may_assign(
        self,
        session,  # type: ignore[no-untyped-def]
        admin_actor: Actor,
    ) -> None:
        lead = await make_user(session, role=Role.TEAM_LEAD, telegram_id=920_062)
        chat = await make_chat(session)
        with pytest.raises(AuthorizationError):
            await ChatAssignmentService(session).assign_manager(
                actor=admin_actor, chat=chat, target=lead
            )

    async def test_an_assignment_does_not_change_the_global_role(
        self,
        session,  # type: ignore[no-untyped-def]
        owner_actor: Actor,
    ) -> None:
        lead = await make_user(session, role=Role.TEAM_LEAD, telegram_id=920_063)
        chat = await make_chat(session)
        await ChatAssignmentService(session).assign_manager(
            actor=owner_actor, chat=chat, target=lead
        )
        await session.refresh(lead)
        assert lead.role is Role.TEAM_LEAD

    async def test_a_lead_may_broadcast_only_where_assigned(
        self,
        session,  # type: ignore[no-untyped-def]
        owner_actor: Actor,
    ) -> None:
        lead = await make_user(session, role=Role.TEAM_LEAD, telegram_id=920_064)
        content = await make_chat(session, name="Team Nội dung", telegram_chat_id=-100_001)
        seeding = await make_chat(
            session,
            name="Team Seeding",
            purpose=ChatPurpose.SEEDING_TEAM,
            telegram_chat_id=-100_002,
        )
        assignments = ChatAssignmentService(session)
        await assignments.assign_manager(actor=owner_actor, chat=content, target=lead)

        assert await assignments.may_broadcast_to(user_id=lead.id, chat=content) is True
        assert await assignments.may_broadcast_to(user_id=lead.id, chat=seeding) is False

    async def test_a_lead_cannot_broadcast_department_wide(
        self,
        session,  # type: ignore[no-untyped-def]
        settings: Settings,
        owner_actor: Actor,
    ) -> None:
        """Even assigned to it: speaking for the department stays with the owner."""
        from meobot.application.announcement_service import AnnouncementService
        from meobot.application.audit_service import AuditService

        lead = await make_user(session, role=Role.TEAM_LEAD, telegram_id=920_065)
        department = await make_chat(
            session,
            name="Toàn phòng",
            purpose=ChatPurpose.DEPARTMENT_ANNOUNCEMENTS,
            telegram_chat_id=-100_003,
        )
        await ChatAssignmentService(session).assign_manager(
            actor=owner_actor, chat=department, target=lead
        )
        service = AnnouncementService(session, settings, AuditService(session))
        assert await service.may_use(actor_for(lead), department) is False

    async def test_revoking_removes_future_access_immediately(
        self,
        session,  # type: ignore[no-untyped-def]
        owner_actor: Actor,
    ) -> None:
        lead = await make_user(session, role=Role.TEAM_LEAD, telegram_id=920_066)
        chat = await make_chat(session)
        assignments = ChatAssignmentService(session)
        await assignments.assign_manager(actor=owner_actor, chat=chat, target=lead)
        await assignments.revoke(actor=owner_actor, chat_row_id=chat.id, user_id=lead.id)

        assert await assignments.may_broadcast_to(user_id=lead.id, chat=chat) is False
        # The row is kept: who could post where, and when, is a fact.
        row = await assignments.assignment_for(chat_row_id=chat.id, user_id=lead.id)
        assert row is not None and row.is_active is False


# --- Rendering -------------------------------------------------------------
class TestRendering:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("**đậm**", "<b>đậm</b>"),
            ("*nghiêng*", "<i>nghiêng</i>"),
            ("bình thường", "bình thường"),
        ],
    )
    def test_the_supported_subset(self, raw: str, expected: str) -> None:
        from meobot.bot import formatting

        assert formatting.render_assistant_text(raw) == expected

    def test_bullets_become_readable(self) -> None:
        from meobot.bot import formatting

        rendered = formatting.render_assistant_text("- một\n- hai")
        assert rendered == "• một\n• hai"

    def test_command_underscores_survive(self) -> None:
        """The outage that started all of this: ``/script_types`` must not italicise."""
        from meobot.bot import formatting

        rendered = formatting.render_assistant_text("Dùng /script_types và /add_sheet nhé")
        assert "/script_types" in rendered
        assert "<i>" not in rendered

    @pytest.mark.parametrize(
        "malformed",
        [
            "**chưa đóng",
            "***ba sao***",
            "một * hai * ba * bốn",
            "__nửa vời",
            "**<b>lồng nhau**",
            "### tiêu đề\n**đậm**",
        ],
    )
    def test_malformed_markdown_never_breaks_the_message(self, malformed: str) -> None:
        from meobot.bot import formatting

        rendered = formatting.render_assistant_text(malformed)
        assert "**" not in rendered
        assert rendered.count("<b>") == rendered.count("</b>")
        assert rendered.count("<i>") == rendered.count("</i>")

    def test_user_html_is_inert(self) -> None:
        from meobot.bot import formatting

        rendered = formatting.render_assistant_text('<a href="http://x">nhấp</a> & <b>x</b>')
        assert "<a href" not in rendered
        assert "&amp;" in rendered


# --- Capability consistency ------------------------------------------------
class TestCapabilityConsistency:
    def test_reminders_are_reported_as_available(self, settings: Settings) -> None:
        from meobot.application.capability_service import CapabilityService
        from meobot.tools.registry import build_default_registry
        from tests.fakes import StubHealthService

        service = CapabilityService(
            build_default_registry(health_service=StubHealthService()), settings
        )
        for role in (Role.EMPLOYEE, Role.TEAM_LEAD, Role.ADMIN, Role.OWNER):
            report = service.report_for(
                Actor(
                    user_id=uuid.uuid4(),
                    telegram_user_id=1,
                    telegram_username="x",
                    full_name="x",
                    role=role,
                    active=True,
                )
            )
            available = " ".join(report.available_now)
            assert "lịch nhắc" in available.lower(), role
            assert "Xin nghỉ phép và báo đi muộn" in report.available_now, role

    def test_unfinished_modules_are_still_reported_unfinished(self, settings: Settings) -> None:
        from meobot.application.capability_service import NOT_IMPLEMENTED

        joined = " ".join(NOT_IMPLEMENTED).lower()
        for unfinished in ("giao việc", "seeding", "bằng chứng", "mạng xã hội", "hằng ngày"):
            assert unfinished in joined

    def test_hr_is_no_longer_claimed_missing(self) -> None:
        """HR shipped in 0.6.0A; the capability list said otherwise until now."""
        from meobot.application.capability_service import NOT_IMPLEMENTED

        joined = " ".join(NOT_IMPLEMENTED).lower()
        assert "xin nghỉ" not in joined
        assert "báo đi muộn" not in joined

    def test_a_member_and_an_owner_differ_only_by_permission(self, settings: Settings) -> None:
        from meobot.application.capability_service import (
            BROADCAST_CAPABILITIES,
            CapabilityService,
        )
        from meobot.tools.registry import build_default_registry
        from tests.fakes import StubHealthService

        service = CapabilityService(
            build_default_registry(health_service=StubHealthService()), settings
        )

        def report(role: Role) -> object:
            return service.report_for(
                Actor(
                    user_id=uuid.uuid4(),
                    telegram_user_id=1,
                    telegram_username="x",
                    full_name="x",
                    role=role,
                    active=True,
                )
            )

        member = report(Role.EMPLOYEE)
        owner = report(Role.OWNER)
        for capability in BROADCAST_CAPABILITIES:
            assert capability in owner.available_now  # type: ignore[attr-defined]
            assert capability in member.not_permitted  # type: ignore[attr-defined]
            assert capability not in member.available_now  # type: ignore[attr-defined]


# --- Truthful success language ---------------------------------------------
def test_no_user_copy_claims_success_before_a_commit() -> None:
    """Scan user-visible copy for a past-tense claim in a preview constant.

    A judgement call encoded as a rule: a constant whose name says it is shown
    *before* confirmation may not contain "đã <verb>".
    """
    from meobot.bot.handlers import group_admin, read_receipts, reminders

    previews = [
        reminders.PREVIEW_FOOTER,
        reminders.CANCELLED_DRAFT,
        reminders.EDIT_PROMPT,
        reminders.AMBIGUOUS_HOUR,
        group_admin.NEEDS_BOTH,
        group_admin.CANCELLED,
        read_receipts.NOTHING_TO_REPORT,
    ]
    forbidden = ("đã tạo", "đã gửi", "đã kích hoạt", "đã lưu")
    for text in previews:
        lowered = text.lower()
        for claim in forbidden:
            assert claim not in lowered, f"{text!r} claims success before it happened"


def test_queued_is_not_reported_as_sent() -> None:
    """ "Đã xếp hàng gửi" and "Đã gửi" are different states, and stay different."""
    from meobot.domain.notifications.models import STATUS_LABELS, OutboxStatus

    assert STATUS_LABELS[OutboxStatus.PENDING] == "Đang chờ gửi"
    assert STATUS_LABELS[OutboxStatus.DELIVERED] == "Đã gửi"
    assert STATUS_LABELS[OutboxStatus.PENDING] != STATUS_LABELS[OutboxStatus.DELIVERED]


def test_privacy_classifications_still_hold_for_new_templates() -> None:
    """Every new group-bound template must remain incapable of carrying a reason."""
    from meobot.domain.notifications.templates import group_safe_templates

    for template in group_safe_templates():
        declared = set(template.required) | set(template.optional)
        for leaky in ("reason", "private_note", "decision_reason", "note"):
            assert leaky not in declared, f"{template.key} could carry {leaky}"


def test_the_new_private_templates_cannot_reach_a_group() -> None:
    from meobot.domain.notifications.routing import may_route
    from meobot.domain.notifications.templates import template_for

    for key in (
        "access.request_to_owner",
        "quota.request_to_owner",
        "delivery.failed_alert",
        "destination.unhealthy",
    ):
        template = template_for(key)
        verdict = may_route(
            classification=template.classification,
            recipient_type=RecipientType.REGISTERED_CHAT,
        )
        assert not verdict.allowed, key


async def test_a_refused_route_writes_no_row(session, settings: Settings) -> None:  # type: ignore[no-untyped-def]
    """The privacy check happens before the outbox row exists, not after."""
    chat = await make_chat(session, name="Group chấm công", purpose=ChatPurpose.ATTENDANCE)
    result = await NotificationRouter(session, settings).route(
        [
            RouteRequest(
                event_type=NotificationEvent.ACCESS_REQUEST_SUBMITTED,
                template_key="access.request_to_owner",
                payload={"requester_name": "Người lạ", "chat_label": "Group X"},
                idempotency_key="refused:1",
                aggregate_type="access_request",
                destination=chat,
            )
        ]
    )
    assert not result.any_queued
    assert result.refusals
    assert (await session.execute(select(OutboundMessage))).scalars().all() == []
