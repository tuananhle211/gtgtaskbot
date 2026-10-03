"""One announcement, several groups, through the ``q_notifications`` worker.

The other dispatch file stops at the outbox rows, because that is where a
handler's responsibility ends. This one picks them up the way production does -
through ``notifications.drain_outbox`` - and checks the four things that decide
whether the sender was told the truth:

* every destination gets its **own** outcome, so one group refusing the bot does
  not make the other two look failed;
* a long announcement arrives **in order**, and part 2 is never sent before part
  1 lands;
* "thử lại nơi lỗi" touches the failed destinations and **only** those - a group
  that already read the announcement must not read it twice;
* the result is reported **proactively**, once, through the outbox.

``_drain`` is called directly rather than through Celery: the task body is
``run_async(...)``, which builds its own event loop and refuses to nest inside
one. What is exercised is the same coroutine the worker runs.
"""

from __future__ import annotations

import itertools
import re
from typing import Any

import pytest
from aiogram import Bot, Dispatcher

from meobot.core.config import Settings
from meobot.db.models.dispatch import (
    MessageDispatch,
    MessageDispatchPart,
    MessageDispatchRecipient,
)
from meobot.db.models.notifications import OutboundMessage
from meobot.domain.dispatch.models import DispatchRecipientStatus, DispatchStatus
from meobot.domain.identity.models import Actor, Role
from meobot.domain.notifications.models import NotificationEvent, OutboxStatus
from meobot.integrations.telegram.notifier import FakeNotifier
from meobot.tasks.celery_app import QUEUE_NOTIFICATIONS, celery_app
from meobot.tasks.notifications import _drain
from meobot.tasks.runtime import TaskContext
from tests.conftest import OWNER_TELEGRAM_ID
from tests.fakes import RecordingSession, SqliteDatabase, StubHealthService
from tests.unit.test_multi_group_dispatch import (
    ANNOUNCEMENT,
    KET_BAN,
    LONG_PARAGRAPH,
    SAYKENG,
    TEST_GROUP,
    add_user,
    owner_says,
    register_the_three,
    rows_of,
)

BASE = 1_600_000
_counter = itertools.count(BASE)


def next_uid() -> int:
    return next(_counter)


class RefusingNotifier(FakeNotifier):
    """A transport that refuses one specific chat, and accepts the rest.

    Modelled on the reported case: MeoBot had been removed from one group and
    was still an admin in the other two. What matters is that the announcement
    still reaches the two, and that the third is reported as its own failure
    rather than as "gửi lỗi" for the whole thing.
    """

    def __init__(self, *, refuse: int, reason: str = "Forbidden: bot is not a member") -> None:
        super().__init__()
        self.refuse = refuse
        self.reason = reason

    async def send(
        self, chat_id: int, text: str, *, reply_markup: dict[str, Any] | None = None
    ) -> bool:
        if chat_id == self.refuse:
            raise RuntimeError(self.reason)
        return await super().send(chat_id, text, reply_markup=reply_markup)


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


async def confirm_to_three(
    dispatcher: Dispatcher, bot: Bot, database: SqliteDatabase, *, body: str = ANNOUNCEMENT
) -> None:
    """Drive the real flow to one confirmed dispatch addressed to three groups.

    The owner is given a real ``users`` row: the delivery summary is a private
    message, and MeoBot cannot open a private chat with somebody it has no
    record of.
    """
    await add_user(database, telegram_id=OWNER_TELEGRAM_ID, role=Role.OWNER, name="Trưởng phòng")
    await register_the_three(database)
    await dispatcher.feed_update(
        bot,
        owner_says(f"Gửi thông báo này vào các group của phòng PR Truyền thông: {body}"),
    )
    await dispatcher.feed_update(bot, owner_says("Tất cả"))
    await dispatcher.feed_update(bot, owner_says("Xác nhận"))


# --- Every destination settles on its own -----------------------------------
async def test_all_three_destinations_receive_the_announcement(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    settings: Settings,
) -> None:
    bot, _session = bot_and_session
    await confirm_to_three(dispatcher, bot, bot_database)

    notifier = FakeNotifier()
    result = await _drain(task_context(bot_database, settings, notifier), None)

    assert result["delivered"] == 3
    assert {chat_id for chat_id, _ in notifier.sent} == {SAYKENG, KET_BAN, TEST_GROUP}
    for _, text in notifier.sent:
        assert "THÔNG BÁO" in text
        # The sentence's own addressing is not part of what a group reads.
        assert "phòng PR Truyền thông:" not in text

    recipients = await rows_of(bot_database, MessageDispatchRecipient)
    assert {row.status for row in recipients} == {DispatchRecipientStatus.DELIVERED}
    dispatches = await rows_of(bot_database, MessageDispatch)
    assert dispatches[0].status is DispatchStatus.COMPLETED
    assert dispatches[0].delivered_count == 3


async def test_one_failure_does_not_block_the_other_two(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    settings: Settings,
) -> None:
    """The reported shape: the bot had been removed from exactly one group."""
    bot, _session = bot_and_session
    await confirm_to_three(dispatcher, bot, bot_database)

    notifier = RefusingNotifier(refuse=KET_BAN)
    result = await _drain(task_context(bot_database, settings, notifier), None)

    assert result["delivered"] == 2
    assert {chat_id for chat_id, _ in notifier.sent} == {SAYKENG, TEST_GROUP}

    recipients = {
        row.telegram_chat_id: row for row in await rows_of(bot_database, MessageDispatchRecipient)
    }
    assert recipients[SAYKENG].status is DispatchRecipientStatus.DELIVERED
    assert recipients[TEST_GROUP].status is DispatchRecipientStatus.DELIVERED
    assert recipients[KET_BAN].status is DispatchRecipientStatus.FAILED

    dispatches = await rows_of(bot_database, MessageDispatch)
    assert dispatches[0].status is DispatchStatus.PARTIALLY_FAILED
    assert dispatches[0].delivered_count == 2
    assert dispatches[0].failed_count == 1


async def test_the_sender_is_told_the_result_without_asking(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    settings: Settings,
) -> None:
    """Somebody who does not know a group missed it does not know to ask."""
    bot, _session = bot_and_session
    await confirm_to_three(dispatcher, bot, bot_database)

    notifier = RefusingNotifier(refuse=KET_BAN)
    await _drain(task_context(bot_database, settings, notifier), None)

    summaries = [
        row
        for row in await rows_of(bot_database, OutboundMessage)
        if row.event_type == NotificationEvent.DISPATCH_SUMMARY.value
    ]
    assert len(summaries) == 1
    payload = summaries[0].safe_payload_json
    assert payload["delivered"] == 2
    assert payload["total"] == 3
    assert "KẾT BẠN BỐN PHƯƠNG" in payload["failed_lines"]
    assert "Saykeng" in payload["delivered_lines"]
    # The report is itself an alert, so failing to deliver it cannot raise an
    # alert about the alert.
    assert summaries[0].is_alert


async def test_the_result_is_reported_once_however_often_the_worker_runs(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    settings: Settings,
) -> None:
    bot, _session = bot_and_session
    await confirm_to_three(dispatcher, bot, bot_database)

    notifier = FakeNotifier()
    await _drain(task_context(bot_database, settings, notifier), None)
    await _drain(task_context(bot_database, settings, notifier), None)

    summaries = [
        row
        for row in await rows_of(bot_database, OutboundMessage)
        if row.event_type == NotificationEvent.DISPATCH_SUMMARY.value
    ]
    assert len(summaries) == 1


# --- Retrying ---------------------------------------------------------------
async def repair(database: SqliteDatabase, chat_id: int) -> None:
    """Somebody adds MeoBot back to the group, or restores its permission.

    A failed delivery teaches the registry that a destination is unusable, and
    that verdict is *not* cleared by pressing "thử lại". It should not be:
    MeoBot has evidence the group is broken and none that it has been fixed, so
    retrying without this step is refused rather than queued.
    """
    from meobot.application.chat_registry_service import ChatRegistryService
    from meobot.domain.notifications.models import DestinationHealth
    from tests.fakes import BOT_ID

    async with database.transaction() as db:
        row = await ChatRegistryService(db).by_telegram_id(
            bot_identity=BOT_ID, telegram_chat_id=chat_id
        )
        assert row is not None
        row.bot_can_send = True
        row.is_active = True
        row.health_status = DestinationHealth.HEALTHY


async def retry(database: SqliteDatabase, settings: Settings) -> tuple[int, int]:
    """Press "🔄 Thử lại nơi lỗi", as the service sees it."""
    from meobot.application.audit_service import AuditService
    from meobot.application.dispatch_service import DispatchService

    owner = Actor(telegram_user_id=OWNER_TELEGRAM_ID, full_name="Owner", role=Role.OWNER)
    async with database.transaction() as db:
        service = DispatchService(db, settings, AuditService(db))
        dispatch = (await rows_of(database, MessageDispatch))[0]
        live = await service.by_id(dispatch.id)
        assert live is not None
        return await service.retry_failed(actor=owner, dispatch=live)


def group_sends(notifier: FakeNotifier) -> list[int]:
    """Only the sends aimed at a group.

    The same drain also delivers the sender's own private "kết quả gửi" card
    and any failure alert, and those are not what a retry test is about.
    """
    return [chat_id for chat_id, _ in notifier.sent if chat_id < 0]


async def test_a_retry_touches_only_the_group_that_failed(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    settings: Settings,
) -> None:
    """A group that already read the announcement must not read it twice."""
    bot, _session = bot_and_session
    await confirm_to_three(dispatcher, bot, bot_database)
    await _drain(task_context(bot_database, settings, RefusingNotifier(refuse=KET_BAN)), None)

    await repair(bot_database, KET_BAN)
    destinations, parts = await retry(bot_database, settings)
    assert destinations == 1
    assert parts == 1

    # The second drain reaches the one group that failed, and no other.
    second = FakeNotifier()
    await _drain(task_context(bot_database, settings, second), None)
    assert group_sends(second) == [KET_BAN]

    recipients = {
        row.telegram_chat_id: row for row in await rows_of(bot_database, MessageDispatchRecipient)
    }
    assert recipients[KET_BAN].status is DispatchRecipientStatus.DELIVERED
    dispatches = await rows_of(bot_database, MessageDispatch)
    assert dispatches[0].status is DispatchStatus.COMPLETED


async def test_retrying_a_group_that_is_still_broken_is_refused_not_queued(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    settings: Settings,
) -> None:
    """Pressing "thử lại" does not make a group work.

    MeoBot knows the bot was removed and has no evidence it was added back, so
    the destination is refused rather than queued - which is what stops the
    same announcement being attempted every time somebody taps the button.
    """
    bot, _session = bot_and_session
    await confirm_to_three(dispatcher, bot, bot_database)
    await _drain(task_context(bot_database, settings, RefusingNotifier(refuse=KET_BAN)), None)

    before = len(await rows_of(bot_database, OutboundMessage))
    await retry(bot_database, settings)
    recipients = {
        row.telegram_chat_id: row for row in await rows_of(bot_database, MessageDispatchRecipient)
    }
    assert recipients[KET_BAN].status is DispatchRecipientStatus.SKIPPED
    assert len(await rows_of(bot_database, OutboundMessage)) == before


async def test_a_retry_uses_a_fresh_idempotency_key(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    settings: Settings,
) -> None:
    """A requeued part must not collide with the settled row it replaces."""
    bot, _session = bot_and_session
    await confirm_to_three(dispatcher, bot, bot_database)
    await _drain(task_context(bot_database, settings, RefusingNotifier(refuse=KET_BAN)), None)

    await repair(bot_database, KET_BAN)
    await retry(bot_database, settings)

    keys = [
        row.idempotency_key
        for row in await rows_of(bot_database, OutboundMessage)
        if row.event_type == NotificationEvent.DISPATCH_PART_PUBLISHED.value
    ]
    assert len(keys) == len(set(keys)), "every queued part needs its own key"
    assert any(":version:2:" in key for key in keys)


# --- Long announcements -----------------------------------------------------
LONG_BODY = "\n\n".join(f"Mục {index}. {LONG_PARAGRAPH}" for index in range(40))


async def test_a_long_announcement_arrives_in_order_one_part_at_a_time(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    settings: Settings,
) -> None:
    """Part 2 is durable from the moment of confirmation, and held until part 1 lands."""
    bot, _session = bot_and_session
    await add_user(
        bot_database, telegram_id=OWNER_TELEGRAM_ID, role=Role.OWNER, name="Trưởng phòng"
    )
    await register_the_three(bot_database)
    await dispatcher.feed_update(bot, owner_says(f"Gửi cho group Saykeng: {LONG_BODY}"))
    await dispatcher.feed_update(bot, owner_says("Xác nhận"))

    parts = await rows_of(bot_database, MessageDispatchPart)
    assert len(parts) > 1

    notifier = FakeNotifier()
    delivered_texts: list[str] = []
    for _ in range(len(parts)):
        await _drain(task_context(bot_database, settings, notifier), None)
        delivered_texts = [text for _, text in notifier.sent]

    # One message per part, in order, and each numbered.
    assert len(delivered_texts) == len(parts)
    for index, text in enumerate(delivered_texts, start=1):
        assert f"({index}/{len(parts)})" in text

    recipients = await rows_of(bot_database, MessageDispatchRecipient)
    assert recipients[0].status is DispatchRecipientStatus.DELIVERED
    assert recipients[0].delivered_parts == len(parts)


async def test_no_part_is_silently_truncated(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    settings: Settings,
) -> None:
    """The behaviour this replaces capped content at 3000 characters."""
    bot, _session = bot_and_session
    await register_the_three(bot_database)
    await dispatcher.feed_update(bot, owner_says(f"Gửi cho group Saykeng: {LONG_BODY}"))
    await dispatcher.feed_update(bot, owner_says("Xác nhận"))

    notifier = FakeNotifier()
    parts = await rows_of(bot_database, MessageDispatchPart)
    for _ in range(len(parts)):
        await _drain(task_context(bot_database, settings, notifier), None)

    # Strip the template's own furniture - heading, part marker, signature -
    # and what is left must be the announcement, character for character.
    arrived = "".join(text for _, text in notifier.sent)
    body = re.sub(r"📢 THÔNG BÁO TỪ TRƯỞNG PHÒNG(?: \(\d+/\d+\))?", "", arrived)
    body = body.replace("— MeoBot gửi thay Trưởng phòng", "")
    assert "".join(body.split()) == "".join(LONG_BODY.split())


async def test_a_failed_later_part_makes_the_destination_a_partial_failure(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    settings: Settings,
) -> None:
    """Neither "đã gửi" nor "chưa gửi được" would be true, so neither is said."""
    bot, _session = bot_and_session
    await register_the_three(bot_database)
    await dispatcher.feed_update(bot, owner_says(f"Gửi cho group Saykeng: {LONG_BODY}"))
    await dispatcher.feed_update(bot, owner_says("Xác nhận"))
    parts = await rows_of(bot_database, MessageDispatchPart)

    # Part 1 lands.
    await _drain(task_context(bot_database, settings, FakeNotifier()), None)
    # Everything after it is refused, permanently.
    await _drain(task_context(bot_database, settings, RefusingNotifier(refuse=SAYKENG)), None)

    recipients = await rows_of(bot_database, MessageDispatchRecipient)
    assert recipients[0].status is DispatchRecipientStatus.PARTIAL_FAILURE
    assert recipients[0].delivered_parts == 1
    assert recipients[0].failed_parts == 1
    # And the parts after the failed one are stopped rather than sent alone.
    outbox = await rows_of(bot_database, OutboundMessage)
    cancelled = [row for row in outbox if row.status is OutboxStatus.CANCELLED]
    assert len(cancelled) == max(0, len(parts) - 2)


async def test_retrying_a_partial_failure_sends_only_the_missing_parts(
    dispatcher: Dispatcher,
    bot_database: SqliteDatabase,
    bot_and_session: tuple[Bot, RecordingSession],
    settings: Settings,
) -> None:
    bot, _session = bot_and_session
    await register_the_three(bot_database)
    await dispatcher.feed_update(bot, owner_says(f"Gửi cho group Saykeng: {LONG_BODY}"))
    await dispatcher.feed_update(bot, owner_says("Xác nhận"))
    parts = await rows_of(bot_database, MessageDispatchPart)

    await _drain(task_context(bot_database, settings, FakeNotifier()), None)
    await _drain(task_context(bot_database, settings, RefusingNotifier(refuse=SAYKENG)), None)

    await repair(bot_database, SAYKENG)
    destinations, requeued = await retry(bot_database, settings)

    assert destinations == 1
    # Part 1 already arrived, so it is not among the ones queued again.
    assert requeued == len(parts) - 1

    second = FakeNotifier()
    for _ in range(len(parts)):
        await _drain(task_context(bot_database, settings, second), None)
    assert all(f"(1/{len(parts)})" not in text for _, text in second.sent)


# --- The queue the worker actually runs -------------------------------------
@pytest.mark.parametrize(
    "task_name",
    ["notifications.drain_outbox", "notifications.recover_stale", "notifications.retry_message"],
)
def test_dispatch_delivery_runs_on_q_notifications(task_name: str) -> None:
    """The multi-group path uses the outbox, so it uses the outbox's queue.

    A route that quietly fell back to ``q_default`` would put department
    announcements behind sheet syncs, which is exactly the starvation
    ``q_notifications`` exists to prevent.
    """
    routes = celery_app.conf.task_routes
    matched = next(
        (options for pattern, options in routes.items() if task_name.startswith(pattern[:-1])),
        None,
    )
    assert matched is not None, f"{task_name} has no route"
    assert matched["queue"] == QUEUE_NOTIFICATIONS
