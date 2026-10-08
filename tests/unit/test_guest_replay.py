"""Answering the stranger's first question, and the two legacy access flows.

The property under test is idempotency across four different ways of asking
twice: the owner double-tapping, Telegram redelivering an update, Celery
retrying a task, and a worker restarting mid-flight. All four converge on one
conditional ``UPDATE``, so all four are tested against the same claim.

The quota tests matter as much as the correctness ones. A Guest gets ten
answers; charging them for an answer they never received would be a bug that
only shows up as "MeoBot stopped talking to me sooner than it said".
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from meobot.application.access_notifications_service import (
    queue_access_request_notification,
    queue_quota_request_notification,
)
from meobot.application.access_request_service import AccessRequestService
from meobot.application.deferred_guest_service import DeferredGuestMessageService
from meobot.application.group_policy_service import GroupPolicyService
from meobot.application.quota_request_service import QuotaRequestService
from meobot.application.quota_service import QuotaService
from meobot.core.config import Settings
from meobot.db.models.access import GroupMemberResponsePolicy, PendingGuestAccessRequest
from meobot.db.models.deferred import DeferredGuestMessage
from meobot.db.models.notifications import OutboundMessage
from meobot.db.models.user import User
from meobot.domain.access.models import (
    GUEST_DEFAULT_DURATION,
    GUEST_DEFAULT_QUESTION_LIMIT,
    UserStatus,
)
from meobot.domain.deferred.models import AuthorizationMode, DeferredMessageStatus
from meobot.domain.identity.models import Actor, Role
from meobot.domain.notifications.models import NotificationEvent
from meobot.domain.notifications.templates import render

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
NOW = datetime(2026, 7, 30, 18, 0, tzinfo=TZ)
GROUP = -100_900
STRANGER = 930_001


async def open_request(
    session,  # type: ignore[no-untyped-def]
    *,
    question: str = "TasksBot giúp được gì cho team nội dung?",
    message_id: int = 42,
) -> PendingGuestAccessRequest:
    request, _notify = await AccessRequestService(session).open_or_reuse(
        bot_id=1,
        chat_id=GROUP,
        chat_title="Nhóm Nội Dung",
        requester_telegram_id=STRANGER,
        requester_username=None,
        requester_display_name="Người Lạ",
        source_message_id=message_id,
        text=question,
    )
    return request


async def capture(
    session,  # type: ignore[no-untyped-def]
    settings: Settings,
    request: PendingGuestAccessRequest,
    *,
    text: str = "TasksBot giúp được gì cho team nội dung?",
) -> DeferredGuestMessage | None:
    return await DeferredGuestMessageService(session, settings).capture(
        request=request, bot_identity=1, text=text, reply_to_message_id=42
    )


class TestHolding:
    async def test_the_question_is_stored_without_touching_a_model(
        self,
        session,  # type: ignore[no-untyped-def]
        settings: Settings,
    ) -> None:
        request = await open_request(session)
        held = await capture(session, settings, request)
        assert held is not None
        assert held.status is DeferredMessageStatus.PENDING_APPROVAL
        assert held.sanitized_text == "TasksBot giúp được gì cho team nội dung?"
        assert held.original_chat_id == GROUP
        assert held.reply_to_message_id == 42

        # Holding is not processing. Capturing a question produces no answer,
        # no conversation memory and no outbound message of its own - the
        # owner's approval card is queued separately, by the access gate.
        assert held.outbox_message_id is None
        assert held.processing_started_at is None
        rows = (await session.execute(select(OutboundMessage))).scalars().all()
        assert all(row.event_type != NotificationEvent.GUEST_REPLY.value for row in rows)

    async def test_five_messages_while_waiting_produce_one_held_question(
        self,
        session,  # type: ignore[no-untyped-def]
        settings: Settings,
    ) -> None:
        """The owner read one question and approved that one."""
        request = await open_request(session)
        for index in range(5):
            await capture(session, settings, request, text=f"câu hỏi {index}")
        rows = (await session.execute(select(DeferredGuestMessage))).scalars().all()
        assert len(rows) == 1
        assert rows[0].sanitized_text == "câu hỏi 0"

    async def test_a_secret_in_the_question_is_redacted_before_storage(
        self,
        session,  # type: ignore[no-untyped-def]
        settings: Settings,
    ) -> None:
        request = await open_request(session)
        held = await capture(
            session,
            settings,
            request,
            text="token của tôi là 123456789:AAEhBOweik6ad9r_QpFGa4Kg0OiJmR0Tabc",
        )
        assert held is not None
        assert "AAEhBOweik6ad9r" not in held.sanitized_text

    async def test_an_empty_question_is_not_stored(
        self,
        session,  # type: ignore[no-untyped-def]
        settings: Settings,
    ) -> None:
        request = await open_request(session)
        assert await capture(session, settings, request, text="   ") is None


class TestAuthorization:
    async def test_answer_once_authorizes_without_creating_guest_access(
        self,
        session,  # type: ignore[no-untyped-def]
        settings: Settings,
    ) -> None:
        request = await open_request(session)
        await capture(session, settings, request)
        service = DeferredGuestMessageService(session, settings)

        held = await service.authorize(
            pending_access_request_id=request.id, mode=AuthorizationMode.ANSWER_ONCE
        )
        assert held is not None
        assert held.status is DeferredMessageStatus.AUTHORIZED_ONCE
        # No Guest window was created by authorising the question.
        assert (await session.execute(select(GroupMemberResponsePolicy))).scalars().all() == []

    async def test_a_second_press_authorizes_nothing_new(
        self,
        session,  # type: ignore[no-untyped-def]
        settings: Settings,
    ) -> None:
        request = await open_request(session)
        await capture(session, settings, request)
        service = DeferredGuestMessageService(session, settings)

        first = await service.authorize(
            pending_access_request_id=request.id, mode=AuthorizationMode.ANSWER_ONCE
        )
        second = await service.authorize(
            pending_access_request_id=request.id, mode=AuthorizationMode.ANSWER_ONCE
        )
        assert first is not None and second is not None
        assert first.id == second.id
        assert second.version == first.version

    async def test_only_one_worker_can_claim_the_question(
        self,
        session,  # type: ignore[no-untyped-def]
        settings: Settings,
    ) -> None:
        """The single statement that makes every retry path safe."""
        request = await open_request(session)
        held = await capture(session, settings, request)
        assert held is not None
        service = DeferredGuestMessageService(session, settings)
        await service.authorize(
            pending_access_request_id=request.id, mode=AuthorizationMode.GUEST_WINDOW
        )

        first = await service.claim_for_processing(held.id)
        second = await service.claim_for_processing(held.id)
        assert first is not None
        assert second is None, "a second claim must produce no second answer"
        assert first.status is DeferredMessageStatus.PROCESSING

    async def test_an_expired_question_is_never_replayed(
        self,
        session,  # type: ignore[no-untyped-def]
        settings: Settings,
    ) -> None:
        """Answering yesterday's message is worse than not answering."""
        request = await open_request(session)
        held = await capture(session, settings, request)
        assert held is not None
        held.expires_at = datetime.now(tz=ZoneInfo("UTC")) - timedelta(hours=1)
        await session.flush()

        service = DeferredGuestMessageService(session, settings)
        assert (
            await service.authorize(
                pending_access_request_id=request.id, mode=AuthorizationMode.GUEST_WINDOW
            )
            is None
        )
        await session.refresh(held)
        assert held.status is DeferredMessageStatus.EXPIRED
        assert held.sanitized_text == ""

    async def test_rejection_purges_the_text_but_keeps_the_hash(
        self,
        session,  # type: ignore[no-untyped-def]
        settings: Settings,
    ) -> None:
        request = await open_request(session)
        held = await capture(session, settings, request)
        assert held is not None
        original_hash = held.original_text_hash

        await DeferredGuestMessageService(session, settings).reject(
            pending_access_request_id=request.id
        )
        await session.refresh(held)
        assert held.status is DeferredMessageStatus.REJECTED
        assert held.sanitized_text == ""
        assert held.original_text_hash == original_hash

    async def test_a_restart_before_processing_preserves_the_question(
        self,
        session,  # type: ignore[no-untyped-def]
        settings: Settings,
    ) -> None:
        """The authorization is durable; a worker that never ran loses nothing."""
        request = await open_request(session)
        held = await capture(session, settings, request)
        assert held is not None
        service = DeferredGuestMessageService(session, settings)
        await service.authorize(
            pending_access_request_id=request.id, mode=AuthorizationMode.GUEST_WINDOW
        )

        # Simulate a fresh process: a new service on the same data.
        reopened = DeferredGuestMessageService(session, settings)
        found = await reopened.for_request(request.id)
        assert found is not None
        assert found.status is DeferredMessageStatus.AUTHORIZED_GUEST
        assert found.sanitized_text

    async def test_a_provider_failure_gives_the_authorization_back(
        self,
        session,  # type: ignore[no-untyped-def]
        settings: Settings,
    ) -> None:
        """The provider failed, not the owner's decision."""
        request = await open_request(session)
        held = await capture(session, settings, request)
        assert held is not None
        service = DeferredGuestMessageService(session, settings)
        await service.authorize(
            pending_access_request_id=request.id, mode=AuthorizationMode.GUEST_WINDOW
        )
        claimed = await service.claim_for_processing(held.id)
        assert claimed is not None

        await service.release_claim(claimed)
        assert claimed.status is DeferredMessageStatus.AUTHORIZED_GUEST
        # And it can be claimed again.
        assert await service.claim_for_processing(held.id) is not None


class TestGuestQuota:
    async def test_a_delivered_answer_costs_exactly_one_of_ten(
        self,
        session,  # type: ignore[no-untyped-def]
        settings: Settings,
        owner_actor: Actor,
    ) -> None:
        policy = await GroupPolicyService(session).grant_guest(
            actor=owner_actor,
            bot_id=1,
            chat_id=GROUP,
            telegram_user_id=STRANGER,
            question_limit=GUEST_DEFAULT_QUESTION_LIMIT,
            duration=GUEST_DEFAULT_DURATION,
        )
        quota = QuotaService(session, settings)
        reservation = await quota.reserve_guest(policy_id=policy.id)
        assert reservation is not None
        await quota.commit(reservation)

        await session.refresh(policy)
        assert policy.guest_questions_used == 1
        assert policy.guest_reserved_count == 0
        remaining = policy.guest_question_limit - policy.guest_questions_used
        assert remaining == 9, "after one answered question, nine remain"

    async def test_a_failed_answer_costs_nothing(
        self,
        session,  # type: ignore[no-untyped-def]
        settings: Settings,
        owner_actor: Actor,
    ) -> None:
        policy = await GroupPolicyService(session).grant_guest(
            actor=owner_actor,
            bot_id=1,
            chat_id=GROUP,
            telegram_user_id=STRANGER,
            question_limit=GUEST_DEFAULT_QUESTION_LIMIT,
            duration=GUEST_DEFAULT_DURATION,
        )
        quota = QuotaService(session, settings)
        reservation = await quota.reserve_guest(policy_id=policy.id)
        assert reservation is not None
        # Generation failed, or Telegram refused: the slot goes back.
        await quota.release(reservation)

        await session.refresh(policy)
        assert policy.guest_questions_used == 0
        assert policy.guest_reserved_count == 0

    async def test_a_guest_cannot_be_charged_past_the_limit(
        self,
        session,  # type: ignore[no-untyped-def]
        settings: Settings,
        owner_actor: Actor,
    ) -> None:
        policy = await GroupPolicyService(session).grant_guest(
            actor=owner_actor,
            bot_id=1,
            chat_id=GROUP,
            telegram_user_id=STRANGER,
            question_limit=2,
            duration=GUEST_DEFAULT_DURATION,
        )
        quota = QuotaService(session, settings)
        for _ in range(2):
            reservation = await quota.reserve_guest(policy_id=policy.id)
            assert reservation is not None
            await quota.commit(reservation)
        assert await quota.reserve_guest(policy_id=policy.id) is None


class TestLegacyAccessFlows:
    """The two 0.5.0 flows that used to call ``bot.send_message`` directly."""

    async def test_the_access_request_goes_to_the_owner_through_the_outbox(
        self,
        session,  # type: ignore[no-untyped-def]
        settings: Settings,
    ) -> None:
        request = await open_request(session)
        queued = await queue_access_request_notification(session, settings, request=request)
        assert queued is not None
        assert queued.telegram_chat_id == settings.meobot_owner_telegram_id
        assert queued.template_key == "access.request_to_owner"

        text = render(queued.template_key, dict(queued.safe_payload_json))
        assert "Người Lạ" in text
        assert "Nhóm Nội Dung" in text
        assert str(STRANGER) not in text

    async def test_queueing_the_same_request_twice_creates_one_row(
        self,
        session,  # type: ignore[no-untyped-def]
        settings: Settings,
    ) -> None:
        request = await open_request(session)
        await queue_access_request_notification(session, settings, request=request)
        await queue_access_request_notification(session, settings, request=request)
        rows = [
            row
            for row in (await session.execute(select(OutboundMessage))).scalars().all()
            if row.event_type == NotificationEvent.ACCESS_REQUEST_SUBMITTED.value
        ]
        assert len(rows) == 1

    async def test_the_request_survives_when_the_owner_cannot_be_reached(
        self,
        session,  # type: ignore[no-untyped-def]
        settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A notification that cannot be queued must not invalidate the request."""
        from meobot.application import access_notifications_service as module
        from meobot.application.recipient_resolver import PrivateResolution

        async def no_owner(self: object) -> PrivateResolution:
            return PrivateResolution(message="không có Trưởng phòng")

        monkeypatch.setattr(module.RecipientResolver, "owner", no_owner)

        request = await open_request(session)
        assert await queue_access_request_notification(session, settings, request=request) is None

        stored = await AccessRequestService(session).by_id(request.id)
        assert stored is not None, "the request must still exist"
        assert stored.status.value == "open"

    async def test_the_quota_request_goes_to_the_owner_through_the_outbox(
        self,
        session,  # type: ignore[no-untyped-def]
        settings: Settings,
    ) -> None:
        user = User(
            telegram_user_id=930_100,
            telegram_username="linh",
            full_name="Nguyễn Thị Linh",
            role=Role.EMPLOYEE,
            active=True,
            status=UserStatus.ACTIVE,
            telegram_private_chat_id=930_100,
            private_chat_available=True,
        )
        session.add(user)
        await session.flush()

        request, created = await QuotaRequestService(session, settings).open_or_reuse(
            user_id=user.id,
            telegram_user_id=user.telegram_user_id,
            display_name=user.full_name,
            chat_id=user.telegram_user_id,
            source_message_id=1,
        )
        assert created
        queued = await queue_quota_request_notification(session, settings, request=request)
        assert queued is not None
        assert queued.telegram_chat_id == settings.meobot_owner_telegram_id

        text = render(queued.template_key, dict(queued.safe_payload_json))
        assert "Nguyễn Thị Linh" in text
        assert "lượt/ngày" in text

    async def test_both_legacy_templates_are_private_chat_only(self) -> None:
        """A stranger's words and a named person's allowance are not group matter."""
        from meobot.domain.notifications.models import RecipientType
        from meobot.domain.notifications.routing import may_route
        from meobot.domain.notifications.templates import template_for

        for key in ("access.request_to_owner", "quota.request_to_owner"):
            template = template_for(key)
            assert not template.classification.may_reach_a_group, key
            verdict = may_route(
                classification=template.classification,
                recipient_type=RecipientType.REGISTERED_CHAT,
            )
            assert not verdict.allowed, key


def test_the_reply_template_carries_only_the_answer() -> None:
    """The group is not told that somebody was vetted."""
    from meobot.domain.notifications.templates import template_for

    template = template_for("guest.reply")
    assert set(template.required) == {"answer"}
    assert template.optional == ()
    assert template.classification.may_reach_a_group

    rendered = template.render({"answer": "TasksBot hỗ trợ nội dung và vận hành."})
    assert rendered == "TasksBot hỗ trợ nội dung và vận hành."


def test_a_guest_reply_key_is_stable_per_question() -> None:
    from meobot.application.notification_router import guest_reply_key

    identifier = uuid.uuid4()
    assert guest_reply_key(identifier) == guest_reply_key(identifier)
    assert guest_reply_key(identifier) != guest_reply_key(uuid.uuid4())
