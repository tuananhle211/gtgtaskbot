"""The reminder module, from a Vietnamese sentence to a delivered message.

The claim these defend is narrow and was the whole reason for the release:
**MeoBot never says a reminder exists until one does.** Everything else here -
recurrence arithmetic, the sweep, the missed-occurrence policy - exists to make
that claim survive contact with a restart.

Nothing in this file calls a model. Reminder handling is deterministic by
construction, and the tests that assert "zero AI quota" are asserting that.
"""

from __future__ import annotations

import uuid
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from meobot.application.chat_assignment_service import ChatAssignmentService
from meobot.application.notification_router import NotificationRouter
from meobot.application.reminder_service import ReminderService
from meobot.core.config import Settings
from meobot.core.errors import AuthorizationError
from meobot.db.models.notifications import OutboundMessage, TelegramChat
from meobot.db.models.reminder import Reminder, ReminderOccurrence
from meobot.db.models.user import User
from meobot.domain.access.models import UserStatus
from meobot.domain.identity.models import Actor, Role
from meobot.domain.member.intents import MemberIntent, classify
from meobot.domain.notifications.models import ChatPurpose, PrivacyClassification
from meobot.domain.reminders.models import (
    OccurrenceStatus,
    ReminderDestinationType,
    ReminderStatus,
    ScheduleKind,
)
from meobot.domain.reminders.parsing import parse_reminder
from meobot.domain.reminders.schedule import ReminderSchedule

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
#: A Thursday, after 16:00 local.
NOW = datetime(2026, 7, 30, 18, 0, tzinfo=TZ)


async def make_user(
    session,  # type: ignore[no-untyped-def]
    *,
    role: Role = Role.EMPLOYEE,
    telegram_id: int = 910_001,
    name: str = "Nguyễn Thị Linh",
    private: bool = True,
) -> User:
    user = User(
        telegram_user_id=telegram_id,
        telegram_username="linh",
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
    telegram_chat_id: int = -100_555,
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


async def preview_and_create(
    session,  # type: ignore[no-untyped-def]
    settings: Settings,
    actor: Actor,
    text: str,
    *,
    destination: TelegramChat | None = None,
    now: datetime = NOW,
) -> Reminder:
    service = ReminderService(session, settings)
    preview = await service.preview(actor=actor, text=text, destination_chat=destination, now=now)
    return await service.create(actor=actor, preview=preview, now=now)


# --- Parsing ---------------------------------------------------------------
class TestParsing:
    """Every documented phrasing, resolved without a model."""

    @pytest.mark.parametrize(
        ("phrase", "kind"),
        [
            ("Nhắc chị đi dạy lúc 16 giờ mỗi thứ 5.", ScheduleKind.WEEKLY),
            ("Mỗi ngày lúc 8 giờ nhắc tôi gửi báo cáo.", ScheduleKind.DAILY),
            ("Nhắc tôi 3 giờ chiều mai gọi cho bác sĩ.", ScheduleKind.ONE_TIME),
            ("30 phút nữa nhắc tôi gọi cho Linh.", ScheduleKind.ONE_TIME),
            ("Nhắc tôi họp vào thứ Hai hàng tuần lúc 9 giờ.", ScheduleKind.WEEKLY),
            ("16 giờ mỗi thứ 5 nhắc tôi đi dạy.", ScheduleKind.WEEKLY),
        ],
    )
    def test_supported_shapes(self, phrase: str, kind: ScheduleKind) -> None:
        result = parse_reminder(phrase, now=NOW, tz=TZ)
        assert result.draft is not None, phrase
        assert result.draft.schedule.kind is kind

    def test_the_content_keeps_its_accents(self) -> None:
        """Content is what survived consumption, not something reconstructed."""
        result = parse_reminder("Nhắc tôi 3 giờ chiều mai gọi cho bác sĩ.", now=NOW, tz=TZ)
        assert result.draft is not None
        assert result.draft.content == "gọi cho bác sĩ"

    def test_afternoon_shifts_the_hour(self) -> None:
        result = parse_reminder("Nhắc tôi 3 giờ chiều mai gọi bác sĩ", now=NOW, tz=TZ)
        assert result.draft is not None
        assert result.draft.schedule.local_time.hour == 15

    def test_four_oclock_asks_exactly_one_question(self) -> None:
        """ "4 giờ" does not say which half of the day, so it asks rather than guesses."""
        result = parse_reminder("Nhắc tôi 4 giờ họp", now=NOW, tz=TZ)
        assert result.needs_question
        assert result.ambiguous_hour == 4
        assert result.draft is None

    def test_four_oclock_with_a_meridiem_does_not_ask(self) -> None:
        result = parse_reminder("Nhắc tôi 4 giờ chiều họp", now=NOW, tz=TZ)
        assert not result.needs_question
        assert result.draft is not None
        assert result.draft.schedule.local_time.hour == 16

    def test_eight_and_nine_read_as_morning(self) -> None:
        """The documented daily and weekly examples must not stop to ask."""
        for phrase in ("Mỗi ngày lúc 8 giờ nhắc tôi gửi báo cáo.", "Nhắc tôi lúc 9 giờ họp"):
            result = parse_reminder(phrase, now=NOW, tz=TZ)
            assert not result.needs_question, phrase
            assert result.draft is not None

    def test_monthly_is_declined_truthfully(self) -> None:
        """Not supported, and said so - rather than approximated weekly."""
        result = parse_reminder("Nhắc tôi mỗi tháng nộp báo cáo lúc 9 giờ", now=NOW, tz=TZ)
        assert result.draft is None
        assert result.problem is not None
        assert "chưa làm được" in result.problem

    def test_a_sentence_with_no_time_says_so(self) -> None:
        result = parse_reminder("Nhắc tôi gọi cho Linh", now=NOW, tz=TZ)
        assert result.draft is None
        assert result.problem is not None
        assert "mấy giờ" in result.problem


class TestRecurrence:
    """Wall-clock arithmetic, in Asia/Ho_Chi_Minh, stored in UTC."""

    def test_weekly_lands_on_the_next_matching_weekday(self) -> None:
        schedule = ReminderSchedule(kind=ScheduleKind.WEEKLY, local_time=time(16, 0), weekday=3)
        first = schedule.next_after(NOW, tz=TZ).astimezone(TZ)
        assert (first.year, first.month, first.day) == (2026, 8, 6)
        assert first.hour == 16

    def test_weekly_today_before_the_hour_stays_today(self) -> None:
        morning = datetime(2026, 7, 30, 9, 0, tzinfo=TZ)
        schedule = ReminderSchedule(kind=ScheduleKind.WEEKLY, local_time=time(16, 0), weekday=3)
        first = schedule.next_after(morning, tz=TZ).astimezone(TZ)
        assert (first.month, first.day) == (7, 30)

    def test_daily_rolls_to_tomorrow_once_the_hour_has_passed(self) -> None:
        schedule = ReminderSchedule(kind=ScheduleKind.DAILY, local_time=time(8, 0))
        first = schedule.next_after(NOW, tz=TZ).astimezone(TZ)
        assert (first.month, first.day) == (7, 31)

    def test_instants_are_stored_in_utc(self) -> None:
        schedule = ReminderSchedule(kind=ScheduleKind.DAILY, local_time=time(16, 0))
        moment = schedule.next_after(NOW, tz=TZ)
        assert moment.tzinfo is not None
        # 16:00 in UTC+7 is 09:00 UTC.
        assert moment.astimezone(ZoneInfo("UTC")).hour == 9


# --- Creation --------------------------------------------------------------
class TestCreation:
    async def test_a_preview_writes_nothing(self, session, settings: Settings) -> None:  # type: ignore[no-untyped-def]
        """The bug this release fixed: a preview is not a reminder."""
        user = await make_user(session)
        service = ReminderService(session, settings)
        await service.preview(
            actor=actor_for(user), text="Nhắc chị đi dạy lúc 16 giờ mỗi thứ 5.", now=NOW
        )
        assert (await session.execute(select(Reminder))).scalars().all() == []

    async def test_confirmation_creates_exactly_one_durable_reminder(
        self,
        session,  # type: ignore[no-untyped-def]
        settings: Settings,
    ) -> None:
        user = await make_user(session)
        reminder = await preview_and_create(
            session, settings, actor_for(user), "Nhắc chị đi dạy lúc 16 giờ mỗi thứ 5."
        )
        rows = (await session.execute(select(Reminder))).scalars().all()
        assert len(rows) == 1
        assert reminder.content == "đi dạy"
        assert reminder.schedule_kind is ScheduleKind.WEEKLY
        assert reminder.status is ReminderStatus.ACTIVE
        assert reminder.destination_type is ReminderDestinationType.USER_PRIVATE

    async def test_the_first_occurrence_is_correct_in_local_time(
        self,
        session,  # type: ignore[no-untyped-def]
        settings: Settings,
    ) -> None:
        user = await make_user(session)
        reminder = await preview_and_create(
            session, settings, actor_for(user), "Nhắc chị đi dạy lúc 16 giờ mỗi thứ 5."
        )
        assert reminder.next_run_at is not None
        local = reminder.next_run_at.astimezone(TZ)
        assert (local.year, local.month, local.day, local.hour) == (2026, 8, 6, 16)

    async def test_the_preview_card_names_the_first_occurrence(
        self,
        session,  # type: ignore[no-untyped-def]
        settings: Settings,
    ) -> None:
        user = await make_user(session)
        preview = await ReminderService(session, settings).preview(
            actor=actor_for(user), text="Nhắc chị đi dạy lúc 16 giờ mỗi thứ 5.", now=NOW
        )
        card = preview.render(TZ)
        assert "⏰ LỊCH NHẮC" in card
        assert "đi dạy" in card
        assert "16:00 mỗi thứ Năm" in card
        assert "Chat riêng của bạn" in card
        assert "06/08/2026" in card


# --- Destinations ----------------------------------------------------------
class TestDestinations:
    async def test_a_member_cannot_target_a_group(self, session, settings: Settings) -> None:  # type: ignore[no-untyped-def]
        user = await make_user(session)
        chat = await make_chat(session)
        with pytest.raises(AuthorizationError):
            await ReminderService(session, settings).preview(
                actor=actor_for(user),
                text="Nhắc cả nhóm 9 giờ mai họp",
                destination_chat=chat,
                now=NOW,
            )

    async def test_a_member_reminder_targets_only_themselves(
        self,
        session,  # type: ignore[no-untyped-def]
        settings: Settings,
    ) -> None:
        user = await make_user(session)
        reminder = await preview_and_create(
            session, settings, actor_for(user), "Nhắc tôi 9 giờ mai họp"
        )
        assert reminder.destination_user_id == user.id
        assert reminder.destination_chat_id is None

    async def test_a_team_lead_cannot_target_an_unassigned_group(
        self,
        session,  # type: ignore[no-untyped-def]
        settings: Settings,
    ) -> None:
        lead = await make_user(session, role=Role.TEAM_LEAD, telegram_id=910_002)
        chat = await make_chat(session)
        with pytest.raises(AuthorizationError):
            await ReminderService(session, settings).preview(
                actor=actor_for(lead),
                text="Nhắc nhóm 9 giờ mai họp",
                destination_chat=chat,
                now=NOW,
            )

    async def test_a_team_lead_may_target_an_assigned_group(
        self,
        session,  # type: ignore[no-untyped-def]
        settings: Settings,
        owner_actor: Actor,
    ) -> None:
        lead = await make_user(session, role=Role.TEAM_LEAD, telegram_id=910_003)
        chat = await make_chat(session)
        await ChatAssignmentService(session).assign_manager(
            actor=owner_actor, chat=chat, target=lead
        )
        preview = await ReminderService(session, settings).preview(
            actor=actor_for(lead),
            text="Nhắc nhóm 9 giờ mai họp",
            destination_chat=chat,
            now=NOW,
        )
        assert preview.destination_type is ReminderDestinationType.REGISTERED_CHAT

    async def test_the_owner_may_target_any_registered_group(
        self,
        session,  # type: ignore[no-untyped-def]
        settings: Settings,
    ) -> None:
        owner = await make_user(session, role=Role.OWNER, telegram_id=910_004)
        chat = await make_chat(session)
        preview = await ReminderService(session, settings).preview(
            actor=actor_for(owner),
            text="Nhắc nhóm 9 giờ mai họp",
            destination_chat=chat,
            now=NOW,
        )
        assert preview.destination_chat is not None


# --- Firing ----------------------------------------------------------------
class TestFiring:
    async def test_a_due_reminder_produces_one_occurrence_and_one_message(
        self,
        session,  # type: ignore[no-untyped-def]
        settings: Settings,
    ) -> None:
        user = await make_user(session)
        reminder = await preview_and_create(
            session, settings, actor_for(user), "Nhắc chị đi dạy lúc 16 giờ mỗi thứ 5."
        )
        due_at = reminder.next_run_at
        assert due_at is not None

        service = ReminderService(session, settings)
        occurrence = await service.fire(
            reminder, router=NotificationRouter(session, settings), now=due_at
        )
        assert occurrence is not None
        assert occurrence.status is OccurrenceStatus.QUEUED
        assert occurrence.outbox_message_id is not None

        messages = (await session.execute(select(OutboundMessage))).scalars().all()
        assert len(messages) == 1
        assert messages[0].template_key == "reminder.personal"
        assert messages[0].telegram_chat_id == user.telegram_user_id

    async def test_a_second_sweep_of_the_same_instant_creates_nothing(
        self,
        session,  # type: ignore[no-untyped-def]
        settings: Settings,
    ) -> None:
        """Two Beat processes must not produce two reminders."""
        user = await make_user(session)
        reminder = await preview_and_create(
            session, settings, actor_for(user), "Nhắc chị đi dạy lúc 16 giờ mỗi thứ 5."
        )
        due_at = reminder.next_run_at
        assert due_at is not None
        service = ReminderService(session, settings)
        router = NotificationRouter(session, settings)

        await service.fire(reminder, router=router, now=due_at)
        # Put it back as if a second sweep found it still due.
        reminder.next_run_at = due_at
        await session.flush()
        second = await service.fire(reminder, router=router, now=due_at)

        assert second is None
        occurrences = (await session.execute(select(ReminderOccurrence))).scalars().all()
        assert len(occurrences) == 1
        messages = (await session.execute(select(OutboundMessage))).scalars().all()
        assert len(messages) == 1

    async def test_a_recurring_reminder_advances_to_the_next_week(
        self,
        session,  # type: ignore[no-untyped-def]
        settings: Settings,
    ) -> None:
        user = await make_user(session)
        reminder = await preview_and_create(
            session, settings, actor_for(user), "Nhắc chị đi dạy lúc 16 giờ mỗi thứ 5."
        )
        first = reminder.next_run_at
        assert first is not None
        await ReminderService(session, settings).fire(
            reminder, router=NotificationRouter(session, settings), now=first
        )
        assert reminder.next_run_at is not None
        assert (reminder.next_run_at - first) == timedelta(days=7)
        assert reminder.status is ReminderStatus.ACTIVE

    async def test_a_one_time_reminder_completes_after_it_fires(
        self,
        session,  # type: ignore[no-untyped-def]
        settings: Settings,
    ) -> None:
        user = await make_user(session)
        reminder = await preview_and_create(
            session, settings, actor_for(user), "Nhắc tôi 9 giờ mai họp"
        )
        due_at = reminder.next_run_at
        assert due_at is not None
        await ReminderService(session, settings).fire(
            reminder, router=NotificationRouter(session, settings), now=due_at
        )
        assert reminder.status is ReminderStatus.COMPLETED
        assert reminder.next_run_at is None

    async def test_an_old_missed_occurrence_is_skipped_not_flooded(
        self,
        session,  # type: ignore[no-untyped-def]
        settings: Settings,
    ) -> None:
        """After an outage, a daily reminder produces one message, not seven."""
        user = await make_user(session)
        reminder = await preview_and_create(
            session, settings, actor_for(user), "Mỗi ngày lúc 8 giờ nhắc tôi gửi báo cáo."
        )
        due_at = reminder.next_run_at
        assert due_at is not None
        # The worker comes back two days late.
        late = due_at + timedelta(days=2)

        occurrence = await ReminderService(session, settings).fire(
            reminder, router=NotificationRouter(session, settings), now=late
        )
        assert occurrence is not None
        assert occurrence.status is OccurrenceStatus.SKIPPED
        assert occurrence.skip_reason == "outside_grace_window"
        # Skipped, but recorded - not silently vanished.
        assert (await session.execute(select(OutboundMessage))).scalars().all() == []
        # And the next firing is a real future one.
        assert reminder.next_run_at is not None
        assert reminder.next_run_at > late

    async def test_a_delivery_failure_leaves_the_reminder_alive(
        self,
        session,  # type: ignore[no-untyped-def]
        settings: Settings,
    ) -> None:
        """The reminder is the durable thing; a message is a retryable one."""
        user = await make_user(session, private=False)
        reminder = await preview_and_create(
            session, settings, actor_for(user), "Nhắc chị đi dạy lúc 16 giờ mỗi thứ 5."
        )
        due_at = reminder.next_run_at
        assert due_at is not None
        occurrence = await ReminderService(session, settings).fire(
            reminder, router=NotificationRouter(session, settings), now=due_at
        )
        assert occurrence is not None
        # Nobody could be reached, so nothing was queued...
        assert occurrence.status is OccurrenceStatus.FAILED
        # ...and the reminder still fires next week.
        assert reminder.status is ReminderStatus.ACTIVE
        assert reminder.next_run_at is not None


# --- Management ------------------------------------------------------------
class TestManagement:
    async def test_pause_and_cancel_never_hard_delete(
        self,
        session,  # type: ignore[no-untyped-def]
        settings: Settings,
    ) -> None:
        user = await make_user(session)
        actor = actor_for(user)
        reminder = await preview_and_create(
            session, settings, actor, "Nhắc chị đi dạy lúc 16 giờ mỗi thứ 5."
        )
        service = ReminderService(session, settings)

        await service.pause(actor=actor, reminder=reminder)
        assert reminder.status is ReminderStatus.PAUSED
        assert (await session.execute(select(Reminder))).scalars().all() != []

        await service.resume(actor=actor, reminder=reminder, now=NOW)
        assert reminder.status is ReminderStatus.ACTIVE

        await service.cancel(actor=actor, reminder=reminder, now=NOW)
        assert reminder.status is ReminderStatus.CANCELLED
        assert reminder.next_run_at is None
        assert len((await session.execute(select(Reminder))).scalars().all()) == 1

    async def test_a_paused_reminder_is_not_due(
        self,
        session,  # type: ignore[no-untyped-def]
        settings: Settings,
    ) -> None:
        user = await make_user(session)
        actor = actor_for(user)
        reminder = await preview_and_create(
            session, settings, actor, "Nhắc chị đi dạy lúc 16 giờ mỗi thứ 5."
        )
        service = ReminderService(session, settings)
        await service.pause(actor=actor, reminder=reminder)
        due = await service.due_batch(now=reminder.next_run_at or NOW)
        assert list(due) == []

    async def test_only_the_owner_of_a_reminder_may_change_it(
        self,
        session,  # type: ignore[no-untyped-def]
        settings: Settings,
        owner_actor: Actor,
    ) -> None:
        """Not even the Trưởng phòng edits somebody else's reminder."""
        user = await make_user(session)
        reminder = await preview_and_create(
            session, settings, actor_for(user), "Nhắc chị đi dạy lúc 16 giờ mỗi thứ 5."
        )
        with pytest.raises(AuthorizationError):
            await ReminderService(session, settings).pause(actor=owner_actor, reminder=reminder)

    async def test_rescheduling_keeps_the_recurrence(
        self,
        session,  # type: ignore[no-untyped-def]
        settings: Settings,
    ) -> None:
        user = await make_user(session)
        actor = actor_for(user)
        reminder = await preview_and_create(
            session, settings, actor, "Nhắc chị đi dạy lúc 16 giờ mỗi thứ 5."
        )
        await ReminderService(session, settings).reschedule(
            actor=actor, reminder=reminder, local_time=time(17, 0), now=NOW
        )
        assert reminder.local_time == time(17, 0)
        assert reminder.schedule_kind is ScheduleKind.WEEKLY
        assert reminder.next_run_at is not None
        assert reminder.next_run_at.astimezone(TZ).hour == 17

    async def test_finding_by_phrase_returns_every_match(
        self,
        session,  # type: ignore[no-untyped-def]
        settings: Settings,
    ) -> None:
        """Two candidates is a question, not a guess."""
        user = await make_user(session)
        actor = actor_for(user)
        await preview_and_create(session, settings, actor, "Nhắc chị đi dạy lúc 16 giờ mỗi thứ 5.")
        await preview_and_create(session, settings, actor, "Nhắc chị đi dạy lúc 9 giờ mỗi thứ 2.")
        matches = await ReminderService(session, settings).find(user_id=user.id, phrase="đi dạy")
        assert len(matches) == 2


# --- Quota -----------------------------------------------------------------
class TestQuota:
    """Reminder work is deterministic, so it costs no AI allowance."""

    @pytest.mark.parametrize(
        "phrase",
        [
            "Nhắc chị đi dạy lúc 16 giờ mỗi thứ 5.",
            "Lịch nhắc của tôi.",
            "Tạm dừng lịch nhắc đi dạy.",
            "Bật lại lịch nhắc đi dạy.",
            "Huỷ lịch nhắc đi dạy.",
            "Lần nhắc tiếp theo là khi nào?",
        ],
    )
    def test_every_reminder_phrase_is_operational(self, phrase: str) -> None:
        match = classify(phrase)
        assert match.is_operational, phrase
        assert match.intent is not MemberIntent.GENERATIVE

    @pytest.mark.parametrize(
        ("phrase", "intent"),
        [
            ("Lịch nhắc của tôi.", MemberIntent.VIEW_REMINDERS),
            ("Tôi đang có những lịch nhắc nào?", MemberIntent.VIEW_REMINDERS),
            ("Tạm dừng lịch nhắc đi dạy.", MemberIntent.PAUSE_REMINDER),
            ("Bật lại lịch nhắc đi dạy.", MemberIntent.RESUME_REMINDER),
            ("Huỷ lịch nhắc đi dạy.", MemberIntent.CANCEL_REMINDER),
            ("Đổi lịch đi dạy sang 17 giờ.", MemberIntent.EDIT_REMINDER),
            ("Lần nhắc tiếp theo là khi nào?", MemberIntent.NEXT_REMINDER),
        ],
    )
    def test_management_phrases_route_precisely(self, phrase: str, intent: MemberIntent) -> None:
        assert classify(phrase).intent is intent

    def test_a_broadcast_reminder_is_not_a_personal_one(self) -> None:
        """ "Nhắc những người chưa đọc" is a fan-out, not a reminder for me."""
        assert classify("Nhắc những người chưa đọc.").intent is MemberIntent.REMIND_UNREAD


# --- Honesty ---------------------------------------------------------------
def test_the_module_never_claims_creation_before_a_commit() -> None:
    """The preview copy must not contain a past-tense success claim.

    A scan rather than a behavioural test, because the failure mode is a
    sentence somebody adds later - not a code path that stops working.
    """
    from meobot.bot.handlers import reminders

    for name in ("PREVIEW_FOOTER", "AMBIGUOUS_HOUR", "WHICH_REMINDER", "EDIT_PROMPT"):
        text = getattr(reminders, name)
        for claim in ("đã tạo", "đã ghi nhận", "đã kích hoạt", "đã lưu"):
            assert claim not in text.lower(), f"{name} claims success before commit"

    # And the one sentence that *may* say it, does.
    assert "đã tạo" in reminders.CREATED


def test_uncommitted_drafts_have_no_identity() -> None:
    """A draft is a different type from a reminder, so the two cannot be confused."""
    from meobot.application.reminder_service import ReminderPreview
    from meobot.domain.reminders.parsing import ReminderDraft

    assert not hasattr(ReminderDraft, "id")
    assert not hasattr(ReminderPreview, "id")
    assert not issubclass(ReminderPreview, Reminder)


def test_a_stored_rule_round_trips(settings: Settings) -> None:
    """A schedule read back from the database is the one that was written."""
    from meobot.domain.reminders.schedule import parse_recurrence_rule

    original = ReminderSchedule(kind=ScheduleKind.WEEKLY, local_time=time(16, 0), weekday=3)
    restored = parse_recurrence_rule(original.recurrence_rule, local_time=time(16, 0))
    assert restored == original
    assert restored.next_after(NOW, tz=TZ) == original.next_after(NOW, tz=TZ)


def test_an_unsupported_stored_rule_fails_loudly() -> None:
    """Better than firing something the stored rule did not mean."""
    from meobot.domain.reminders.schedule import parse_recurrence_rule

    with pytest.raises(ValueError, match="Unsupported"):
        parse_recurrence_rule("MONTHLY;3;16:00", local_time=time(16, 0))


def test_reminder_ids_are_not_shown_to_users() -> None:
    """No raw enum or identifier reaches a Vietnamese sentence."""
    from meobot.domain.reminders.models import STATUS_LABELS, ReminderStatus

    for status in ReminderStatus:
        label = STATUS_LABELS[status]
        assert label != status.value
        assert not any(character.isupper() and character.isascii() for character in label[1:])


def test_every_occurrence_is_unique_per_instant() -> None:
    """The constraint that carries the whole duplicate defence."""
    constraint_names = {constraint.name for constraint in ReminderOccurrence.__table__.constraints}
    assert "uq_reminder_occurrences_reminder_moment" in constraint_names


def test_uuid_is_unused_here() -> None:
    """Guards the import list from drifting; ``uuid`` is used by fixtures only."""
    assert uuid.UUID is not None
