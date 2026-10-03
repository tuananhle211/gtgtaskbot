"""The four bugs 0.6.0a2 was opened to fix, each pinned by a test.

These were written **before** the fixes and each one failed for the reason its
docstring names. They are kept, rather than folded into the feature suites,
because each is a specific promise a user was given and did not get:

* **A** - asked to introduce itself in a group, MeoBot introduced itself as the
  person who asked.
* **B** - ``**Phương Nhung**`` reached a real chat with the asterisks visible.
* **C** - MeoBot said it had recorded a reminder that did not exist and would
  never fire.
* **D** - after the owner approved a stranger, the question that triggered the
  approval was silently dropped.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from meobot.bot import formatting
from meobot.core.config import Settings
from meobot.domain.identity.models import Actor, Role
from meobot.domain.member.intents import MemberIntent, classify
from meobot.domain.reminders.parsing import parse_reminder
from meobot.domain.reminders.schedule import ScheduleKind

TZ = ZoneInfo("Asia/Ho_Chi_Minh")

#: A Thursday, after 16:00 local - so "16 giờ mỗi thứ Năm" must land on the
#: *following* Thursday rather than today.
NOW = datetime(2026, 7, 30, 18, 0, tzinfo=TZ)


# --- Reproduction A: the assistant introduced itself as the user ------------
class TestReproductionAssistantIdentity:
    """ "@bot chào các anh chị và giới thiệu về em đi" in a group.

    The bot answered *"Em là Phương Nhung, hiện phụ trách vai trò Trưởng
    phòng"* - it read ``[CURRENT USER]`` and spoke as that person. Two things
    were missing: an explicit instruction that MeoBot is never the current
    user, and a group context that withholds the private profile it was
    reading.
    """

    def test_assistant_identity_states_it_is_not_the_current_user(self) -> None:
        from meobot.domain.assistant.identity import NEVER_IMPERSONATE_RULE

        assert "không phải người dùng hiện tại" in NEVER_IMPERSONATE_RULE
        assert "MeoBot" in NEVER_IMPERSONATE_RULE

    def test_system_prompt_carries_the_rule(self) -> None:
        from meobot.integrations.llm.prompts import SYSTEM_PROMPT

        assert "không phải người dùng hiện tại" in SYSTEM_PROMPT

    def test_group_context_withholds_the_private_profile(self, settings: Settings) -> None:
        """A group turn must not carry the actor's private profile.

        The bug was not only that the model spoke as Phương Nhung - it is that
        it *could*, because her job title, priorities and notes were in the
        prompt for a message everybody in the group can read.
        """
        from meobot.application.capability_service import CapabilityService
        from meobot.application.prompt_context_service import (
            ChatScope,
            PromptContextService,
            TurnContext,
        )
        from meobot.domain.assistant.profile import default_assistant_profile
        from meobot.domain.identity.profile import ActorProfile
        from meobot.tools.registry import build_default_registry
        from tests.fakes import StubHealthService

        profile = ActorProfile(
            display_name="Phương Nhung",
            role=Role.OWNER,
            job_title="Trưởng phòng PR Truyền thông",
            profile_notes="Ghi chú riêng không được lộ ra group",
            current_priorities=("Chiến dịch nội bộ",),
        )
        actor = Actor(
            user_id=uuid.uuid4(),
            telegram_user_id=1,
            telegram_username="pn",
            full_name="Phương Nhung",
            role=Role.OWNER,
            active=True,
        )
        service = PromptContextService(
            CapabilityService(build_default_registry(health_service=StubHealthService()), settings),
            settings,
        )
        context = service.build(
            TurnContext(
                message="chào các anh chị và giới thiệu về em đi",
                actor=actor,
                assistant=default_assistant_profile(),
                profile=profile,
                scope=ChatScope.GROUP,
            )
        )
        rendered = context.render()

        assert "Ghi chú riêng không được lộ ra group" not in rendered
        assert "Chiến dịch nội bộ" not in rendered
        # The identity rule must still be present - that is what stops the
        # model borrowing the name that legitimately remains in context.
        assert "không phải người dùng hiện tại" in rendered

    def test_private_context_may_personalize(self, settings: Settings) -> None:
        from meobot.application.capability_service import CapabilityService
        from meobot.application.prompt_context_service import (
            ChatScope,
            PromptContextService,
            TurnContext,
        )
        from meobot.domain.assistant.profile import default_assistant_profile
        from meobot.domain.identity.profile import ActorProfile
        from meobot.tools.registry import build_default_registry
        from tests.fakes import StubHealthService

        actor = Actor(
            user_id=uuid.uuid4(),
            telegram_user_id=1,
            telegram_username="pn",
            full_name="Phương Nhung",
            role=Role.OWNER,
            active=True,
        )
        service = PromptContextService(
            CapabilityService(build_default_registry(health_service=StubHealthService()), settings),
            settings,
        )
        context = service.build(
            TurnContext(
                message="em là ai?",
                actor=actor,
                assistant=default_assistant_profile(),
                profile=ActorProfile(display_name="Phương Nhung", role=Role.OWNER),
                scope=ChatScope.PRIVATE,
            )
        )
        rendered = context.render()
        assert "Phương Nhung" in rendered
        assert "không phải người dùng hiện tại" in rendered


# --- Reproduction B: raw Markdown reached the chat --------------------------
class TestReproductionRawMarkdown:
    """Model output went through ``formatting.escape`` and nothing else.

    ``escape`` protects ``<``, ``>`` and ``&``. It has no opinion about ``**``,
    so a model that wrote ``**Thời gian:**`` produced a message with the
    asterisks visible.
    """

    def test_bold_becomes_html(self) -> None:
        assert formatting.render_assistant_text("**Phương Nhung**") == "<b>Phương Nhung</b>"

    def test_no_raw_markers_survive(self) -> None:
        rendered = formatting.render_assistant_text("**Thời gian:** 16:00\n**Nội dung:** Đi dạy")
        assert "**" not in rendered
        assert "<b>Thời gian:</b>" in rendered

    def test_user_html_is_still_escaped(self) -> None:
        rendered = formatting.render_assistant_text("<script>alert(1)</script>")
        assert "<script>" not in rendered
        assert "&lt;script&gt;" in rendered

    def test_unmatched_markers_do_not_leak(self) -> None:
        """Malformed Markdown must degrade, not produce broken entities."""
        rendered = formatting.render_assistant_text("**chưa đóng và <b> lạc")
        assert "**" not in rendered
        assert rendered.count("<b>") == rendered.count("</b>")


# --- Reproduction C: a reminder that was never created ----------------------
class TestReproductionFalseReminder:
    """ "Nhắc chị đi dạy lúc 16 giờ mỗi thứ 5."

    MeoBot answered that it had "ghi nhận" the reminder. Nothing was written,
    nothing was scheduled, and the user found out by the reminder not arriving.
    The message was classified ``GENERATIVE`` and answered by the model, which
    had no way to know it could not create anything.
    """

    def test_the_message_is_recognised_as_a_reminder(self) -> None:
        match = classify("Nhắc chị đi dạy lúc 16 giờ mỗi thứ 5.")
        assert match.intent is MemberIntent.CREATE_REMINDER
        # Operational: deterministic, free, and never answered by the model.
        assert match.is_operational

    def test_the_schedule_is_parsed_without_a_model(self) -> None:
        result = parse_reminder("Nhắc chị đi dạy lúc 16 giờ mỗi thứ 5.", now=NOW, tz=TZ)
        assert result.draft is not None
        draft = result.draft
        assert draft.schedule.kind is ScheduleKind.WEEKLY
        assert draft.schedule.weekday == 3  # Thursday
        assert draft.schedule.local_time.hour == 16
        assert draft.content == "đi dạy"

    def test_first_occurrence_is_the_next_thursday(self) -> None:
        result = parse_reminder("Nhắc chị đi dạy lúc 16 giờ mỗi thứ 5.", now=NOW, tz=TZ)
        assert result.draft is not None
        first = result.draft.schedule.next_after(NOW, tz=TZ).astimezone(TZ)
        assert (first.year, first.month, first.day) == (2026, 8, 6)
        assert (first.hour, first.minute) == (16, 0)


# --- Reproduction D: the Guest's first question was dropped -----------------
class TestReproductionGuestQuestionLost:
    """The owner approved a stranger, and the stranger got silence.

    The question that caused the approval card existed only as a preview string
    on the pending request. Granting Guest access created the allowance and
    stopped, so the person had to ask again - having already waited for a human
    decision.
    """

    async def test_the_original_question_is_stored_for_replay(self, session) -> None:  # type: ignore[no-untyped-def]
        from meobot.application.access_request_service import AccessRequestService
        from meobot.application.deferred_guest_service import DeferredGuestMessageService
        from meobot.core.config import get_settings
        from meobot.domain.deferred.models import DeferredMessageStatus

        settings = get_settings()
        requests = AccessRequestService(session)
        request, _ = await requests.open_or_reuse(
            bot_id=1,
            chat_id=-100,
            chat_title="Group thử",
            requester_telegram_id=555,
            requester_display_name="Người lạ",
            requester_username=None,
            text="MeoBot giúp được gì cho team nội dung?",
            source_message_id=42,
        )
        service = DeferredGuestMessageService(session, settings)
        stored = await service.capture(
            request=request,
            bot_identity=1,
            text="MeoBot giúp được gì cho team nội dung?",
            reply_to_message_id=42,
        )

        assert stored is not None
        assert stored.status is DeferredMessageStatus.PENDING_APPROVAL
        assert stored.sanitized_text == "MeoBot giúp được gì cho team nội dung?"
        assert stored.original_chat_id == -100
        assert stored.original_message_id == 42

    async def test_owner_approval_authorizes_the_stored_question(self, session) -> None:  # type: ignore[no-untyped-def]
        from meobot.application.access_request_service import AccessRequestService
        from meobot.application.deferred_guest_service import DeferredGuestMessageService
        from meobot.core.config import get_settings
        from meobot.domain.deferred.models import AuthorizationMode, DeferredMessageStatus

        settings = get_settings()
        requests = AccessRequestService(session)
        request, _ = await requests.open_or_reuse(
            bot_id=1,
            chat_id=-100,
            chat_title="Group thử",
            requester_telegram_id=555,
            requester_display_name="Người lạ",
            requester_username=None,
            text="Cho hỏi lịch họp tuần này",
            source_message_id=42,
        )
        service = DeferredGuestMessageService(session, settings)
        await service.capture(
            request=request,
            bot_identity=1,
            text="Cho hỏi lịch họp tuần này",
            reply_to_message_id=42,
        )

        authorized = await service.authorize(
            pending_access_request_id=request.id,
            mode=AuthorizationMode.ANSWER_ONCE,
        )
        assert authorized is not None
        assert authorized.status is DeferredMessageStatus.AUTHORIZED_ONCE

    async def test_rejection_purges_the_stored_text(self, session) -> None:  # type: ignore[no-untyped-def]
        """A refused stranger's words must not stay in the database."""
        from meobot.application.access_request_service import AccessRequestService
        from meobot.application.deferred_guest_service import DeferredGuestMessageService
        from meobot.core.config import get_settings
        from meobot.domain.deferred.models import DeferredMessageStatus

        settings = get_settings()
        requests = AccessRequestService(session)
        request, _ = await requests.open_or_reuse(
            bot_id=1,
            chat_id=-100,
            chat_title="Group thử",
            requester_telegram_id=555,
            requester_display_name="Người lạ",
            requester_username=None,
            text="Nội dung riêng tư của người lạ",
            source_message_id=42,
        )
        service = DeferredGuestMessageService(session, settings)
        stored = await service.capture(
            request=request,
            bot_identity=1,
            text="Nội dung riêng tư của người lạ",
            reply_to_message_id=42,
        )
        assert stored is not None

        await service.reject(pending_access_request_id=request.id)
        await session.refresh(stored)

        assert stored.status is DeferredMessageStatus.REJECTED
        assert stored.sanitized_text == ""
        # The hash survives so an audit can still prove which message this was.
        assert stored.original_text_hash


@pytest.mark.parametrize(
    "phrase",
    [
        "Nhắc tôi 3 giờ chiều mai gọi cho bác sĩ.",
        "Mỗi ngày lúc 8 giờ nhắc tôi gửi báo cáo.",
        "30 phút nữa nhắc tôi gọi cho Linh.",
        "Nhắc tôi họp vào thứ Hai hàng tuần lúc 9 giờ.",
    ],
)
def test_other_reminder_phrasings_are_also_operational(phrase: str) -> None:
    """Every documented phrasing must avoid the model entirely."""
    assert classify(phrase).intent is MemberIntent.CREATE_REMINDER
