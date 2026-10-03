"""From "Gửi ... vào group Test" to the message arriving, through the worker.

The other hotfix files stop at the outbox row, because that is where the
handler's responsibility ends. This one picks the row up the way production
does - through ``notifications.drain_outbox``, the task the ``q_notifications``
worker runs - and checks the three things that decide whether the owner was told
the truth:

* the message goes to the **registered** chat id, not to anything parsed out of
  the sentence;
* a delivery failure leaves the business record intact, so the announcement
  still exists and can be retried;
* a retry does not create a second intent.

``_drain`` is called directly rather than through Celery: the task body is
``run_async(...)``, which builds its own event loop and refuses to nest inside
one. What is exercised is the same coroutine the worker runs.
"""

from __future__ import annotations

import itertools
import uuid

from aiogram import Bot, Dispatcher

from meobot.core.config import Settings
from meobot.db.models.notifications import Announcement, OutboundMessage
from meobot.domain.notifications.models import (
    AnnouncementStatus,
    OutboxStatus,
    RecipientType,
)
from meobot.integrations.telegram.notifier import FakeNotifier
from meobot.tasks.notifications import _drain
from meobot.tasks.runtime import TaskContext
from tests.conftest import OWNER_TELEGRAM_ID
from tests.fakes import (
    RecordingSession,
    SqliteDatabase,
    StubHealthService,
    drawn_button,
    make_update,
)
from tests.unit.test_hotfix_send_and_register import (
    TEST_GROUP,
    press,
    register_test_group,
    rows_of,
)

BASE = 1_400_000
_counter = itertools.count(BASE)


def next_uid() -> int:
    return next(_counter)


def task_context(
    database: SqliteDatabase, settings: Settings, notifier: FakeNotifier
) -> TaskContext:
    """The collaborators ``_drain`` needs, with Telegram replaced by a recorder."""
    stub = StubHealthService()
    return TaskContext(
        settings=settings,
        database=database,  # type: ignore[arg-type]
        sheets=stub,  # type: ignore[arg-type]
        drive=stub,  # type: ignore[arg-type]
        llm=stub,  # type: ignore[arg-type]
        notifier=notifier,
    )


async def queue_a_message(
    dispatcher: Dispatcher,
    bot: Bot,
    session: RecordingSession,
    database: SqliteDatabase,
    *,
    sentence: str = "Gửi Chào buổi sáng vào group Test.",
) -> uuid.UUID:
    """Drive the real flow up to one confirmed, queued announcement."""
    await register_test_group(database)
    await dispatcher.feed_update(
        bot,
        make_update(
            sentence,
            user_id=OWNER_TELEGRAM_ID,
            chat_id=OWNER_TELEGRAM_ID,
            update_id=next_uid(),
        ),
    )
    confirm = drawn_button(session, "✅ Gửi thông báo")
    await dispatcher.feed_update(bot, press(confirm, update_id=next_uid()))
    queued = await rows_of(database, OutboundMessage)
    assert len(queued) == 1, "the confirmation should have queued exactly one message"
    return queued[0].id


async def test_the_worker_delivers_to_the_registered_chat_id(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    settings: Settings,
) -> None:
    """The address is the registry's, never the sentence's."""
    bot, session = bot_and_session
    await queue_a_message(dispatcher, bot, session, bot_database)

    notifier = FakeNotifier()
    result = await _drain(task_context(bot_database, settings, notifier), None)

    assert result["delivered"] == 1
    assert len(notifier.sent) == 1
    chat_id, text = notifier.sent[0]
    assert chat_id == TEST_GROUP
    assert "Chào buổi sáng" in text
    # And the sentence's own addressing is not part of what the group reads.
    assert "vào group" not in text

    rows = await rows_of(bot_database, OutboundMessage)
    assert rows[0].status is OutboxStatus.DELIVERED
    assert rows[0].recipient_type is RecipientType.REGISTERED_CHAT


async def test_a_second_confirmation_does_not_queue_a_second_message(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    settings: Settings,
) -> None:
    """Two presses of one button is one announcement, in the group and here."""
    bot, session = bot_and_session
    await register_test_group(bot_database)
    await dispatcher.feed_update(
        bot,
        make_update(
            "Gửi Chào buổi sáng vào group Test.",
            user_id=OWNER_TELEGRAM_ID,
            chat_id=OWNER_TELEGRAM_ID,
            update_id=next_uid(),
        ),
    )
    confirm = drawn_button(session, "✅ Gửi thông báo")
    await dispatcher.feed_update(bot, press(confirm, update_id=next_uid()))
    await dispatcher.feed_update(bot, press(confirm, update_id=next_uid()))

    assert len(await rows_of(bot_database, OutboundMessage)) == 1
    assert len(await rows_of(bot_database, Announcement)) == 1

    notifier = FakeNotifier()
    await _drain(task_context(bot_database, settings, notifier), None)
    assert len(notifier.sent) == 1, "the group must not read it twice"


async def test_a_delivery_failure_keeps_the_business_record(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    settings: Settings,
) -> None:
    """Telegram refusing is not the announcement never having happened.

    This is the case in the smoke checklist where send permission is removed
    from the group: the owner confirmed, so the announcement is real, and what
    failed is one delivery attempt of it.
    """
    bot, session = bot_and_session
    message_id = await queue_a_message(dispatcher, bot, session, bot_database)

    class RefusingNotifier(FakeNotifier):
        async def send(self, chat_id: int, text: str, **kwargs: object) -> bool:
            self.sent.append((chat_id, text))
            return False

    result = await _drain(task_context(bot_database, settings, RefusingNotifier()), None)
    assert result["failed"] == 1

    published = await rows_of(bot_database, Announcement)
    assert len(published) == 1
    assert published[0].status is AnnouncementStatus.PUBLISHED, (
        "a failed delivery must not un-publish what the owner confirmed"
    )

    rows = await rows_of(bot_database, OutboundMessage)
    assert len(rows) == 1, "a failure must not fork the intent"
    assert rows[0].id == message_id
    assert rows[0].status is not OutboxStatus.DELIVERED
    assert rows[0].attempt_count >= 1


async def test_a_retry_reuses_the_same_intent(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    settings: Settings,
) -> None:
    """Permission restored, retried: one row, one delivery, same message."""
    bot, session = bot_and_session
    message_id = await queue_a_message(dispatcher, bot, session, bot_database)

    class RefusingNotifier(FakeNotifier):
        async def send(self, chat_id: int, text: str, **kwargs: object) -> bool:
            self.sent.append((chat_id, text))
            return False

    await _drain(task_context(bot_database, settings, RefusingNotifier()), None)

    # The operator fixes the group and asks for a retry.
    from meobot.application.outbox_service import OutboxService

    async with bot_database.transaction() as db:
        outbox = OutboxService(db, settings)
        row = await outbox.by_id(message_id)
        assert row is not None
        await outbox.retry_now(row)

    notifier = FakeNotifier()
    result = await _drain(task_context(bot_database, settings, notifier), None)

    assert result["delivered"] == 1
    assert [chat for chat, _ in notifier.sent] == [TEST_GROUP]
    rows = await rows_of(bot_database, OutboundMessage)
    assert len(rows) == 1, "a retry sends the existing intent again, it does not make a new one"
    assert rows[0].id == message_id
    assert rows[0].status is OutboxStatus.DELIVERED


async def test_the_outbox_is_the_only_cross_chat_path(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    settings: Settings,
) -> None:
    """Nothing reached the group until the worker ran.

    Both halves matter: the aiogram transport saw no message for ``TEST_GROUP``
    while the handler was running, and the notifier saw exactly one afterwards.
    """
    bot, session = bot_and_session
    await queue_a_message(dispatcher, bot, session, bot_database)

    to_group = [
        request
        for request in session.sent_of("SendMessage")
        if int(getattr(request, "chat_id", 0) or 0) == TEST_GROUP
    ]
    assert to_group == [], "a handler sent into the destination group directly"

    notifier = FakeNotifier()
    await _drain(task_context(bot_database, settings, notifier), None)
    assert [chat for chat, _ in notifier.sent] == [TEST_GROUP]


async def test_the_queue_name_is_the_one_the_worker_consumes() -> None:
    """The outbox drain is routed to ``q_notifications`` and nothing else."""
    from meobot.tasks.celery_app import QUEUE_NOTIFICATIONS, celery_app

    # Resolved through Celery's own router rather than read off the config:
    # the routes are glob patterns, and what matters is where this task lands.
    resolved = celery_app.amqp.router.route({}, "notifications.drain_outbox")
    assert resolved["queue"].name == QUEUE_NOTIFICATIONS
    assert QUEUE_NOTIFICATIONS == "q_notifications"
    # The task is registered, so a worker on that queue actually has it.
    assert "notifications.drain_outbox" in celery_app.tasks


async def test_the_compose_worker_listens_on_that_queue() -> None:
    """The queue exists in code *and* in the container that drains it."""
    import pathlib

    compose = (pathlib.Path(__file__).resolve().parents[2] / "docker-compose.yml").read_text(
        encoding="utf-8"
    )
    assert "q_notifications" in compose


async def test_the_sender_is_told_dagui_only_after_telegram_settles(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    settings: Settings,
) -> None:
    """Two messages, two truths: "xếp hàng gửi" then "đã gửi".

    The confirmation is queued through the outbox in the same transaction that
    marks the announcement delivered, so it needs a second drain to go out -
    which is exactly right: it is a separate message to a separate chat, and it
    obeys the same rule as everything else.
    """
    from meobot.db.models.user import User
    from meobot.domain.access.models import UserStatus
    from meobot.domain.identity.models import Role

    bot, session = bot_and_session
    # A materialised owner: the confirmation is addressed to a durable user.
    async with bot_database.transaction() as db:
        db.add(
            User(
                telegram_user_id=OWNER_TELEGRAM_ID,
                telegram_username="owner",
                full_name="Trưởng phòng",
                role=Role.OWNER,
                active=True,
                status=UserStatus.ACTIVE,
                telegram_private_chat_id=OWNER_TELEGRAM_ID,
                private_chat_available=True,
            )
        )

    await queue_a_message(dispatcher, bot, session, bot_database)

    # The source chat was told it was queued, and nothing stronger.
    source = session.combined_text()
    assert "xếp hàng gửi" in source
    assert "Đã gửi thông báo tới" not in source

    context = task_context(bot_database, settings, FakeNotifier())
    first = await _drain(context, None)
    assert first["delivered"] == 1

    notifier = FakeNotifier()
    await _drain(task_context(bot_database, settings, notifier), None)
    confirmations = [text for chat, text in notifier.sent if chat == OWNER_TELEGRAM_ID]
    assert len(confirmations) == 1
    assert "Đã gửi thông báo tới Group Test" in confirmations[0]


async def test_the_delivery_confirmation_is_sent_once(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    settings: Settings,
) -> None:
    """A worker that settles twice must not congratulate the owner twice."""
    from meobot.db.models.user import User
    from meobot.domain.access.models import UserStatus
    from meobot.domain.identity.models import Role

    bot, session = bot_and_session
    async with bot_database.transaction() as db:
        db.add(
            User(
                telegram_user_id=OWNER_TELEGRAM_ID,
                telegram_username="owner",
                full_name="Trưởng phòng",
                role=Role.OWNER,
                active=True,
                status=UserStatus.ACTIVE,
                telegram_private_chat_id=OWNER_TELEGRAM_ID,
                private_chat_available=True,
            )
        )
    await queue_a_message(dispatcher, bot, session, bot_database)

    for _ in range(3):
        await _drain(task_context(bot_database, settings, FakeNotifier()), None)

    rows = await rows_of(bot_database, OutboundMessage)
    confirmations = [row for row in rows if row.event_type == "announcement_delivered"]
    assert len(confirmations) == 1
