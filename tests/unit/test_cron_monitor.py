"""The job monitor ("Tác vụ định kỳ", 0077): runs, incidents, alerts, "Chạy lại";
per-job alerts and recipients (0080)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from celery.schedules import crontab
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.api.routers.cron import get_cron_service
from meobot.application.cron.monitor_service import (
    HEADER_TRIGGER,
    HEADER_USER,
    CronMonitorService,
)
from meobot.application.cron.recorder import CronRecorder, RunOutcome, outcome_of
from meobot.application.cron.schedule import expected_after, scheduled_jobs
from meobot.application.org.departments import place
from meobot.bot.cron_keyboards import rerun_binding, rerun_keyboard
from meobot.core.config import Settings
from meobot.core.time import utcnow
from meobot.db.models.cron import CronJob, CronJobIncident, CronJobRun
from meobot.db.models.notifications import OutboundMessage
from meobot.db.models.user import User
from meobot.db.models.user_notification import UserNotification
from meobot.domain.access.models import UserStatus
from meobot.domain.cron.models import (
    CronJobState,
    CronRunStatus,
    CronTrigger,
    after_failure,
    default_policy,
    is_late,
    job_state,
)
from meobot.domain.identity.models import Role
from meobot.domain.member.callbacks import MemberBinding, parse
from meobot.domain.units.models import UnitCode, UnitMemberRole
from tests.unit.pr_world import World
from tests.unit.streams import ensure_units

T0 = datetime(2026, 10, 27, 1, 0, tzinfo=UTC)
OWNER_CHAT = 930_001

BEAT: dict[str, dict[str, Any]] = {
    "outbox": {"task": "notifications.drain_outbox", "schedule": 20.0},
    "heartbeat": {"task": "system.periodic_heartbeat", "schedule": 300.0},
    # 08:00 and 23:00 in Vietnam = 01:00 and 16:00 UTC.
    "ads-spend": {"task": "ads.sync_spend", "schedule": crontab(hour="1,16", minute="0")},
}
JOBS = {job.key: job for job in scheduled_jobs(BEAT)}
DAILY = "ads.sync_spend"
FREQUENT = "notifications.drain_outbox"


# --- the rules -------------------------------------------------------------


def test_01_a_daily_job_retries_twice_then_reports() -> None:
    policy = default_policy(86400)
    assert (policy.retries, policy.retry_delay_seconds, policy.alert_after) == (2, 1800, 1)
    first = after_failure(policy=policy, attempt=1, consecutive=1)
    assert (first.retry_in_seconds, first.next_attempt, first.alert) == (1800, 2, False)
    second = after_failure(policy=policy, attempt=2, consecutive=2)
    assert (second.retry_in_seconds, second.next_attempt, second.alert) == (1800, 3, False)
    third = after_failure(policy=policy, attempt=3, consecutive=3)
    assert (third.retry_in_seconds, third.alert) == (None, True)


def test_02_a_frequent_job_never_retries_and_reports_after_five() -> None:
    policy = default_policy(20)
    assert (policy.retries, policy.alert_after, policy.keep_successes) == (0, 5, False)
    assert [after_failure(policy=policy, attempt=1, consecutive=n).alert for n in range(1, 7)] == [
        False,
        False,
        False,
        False,
        True,
        True,
    ]
    assert after_failure(policy=policy, attempt=1, consecutive=9).retry_in_seconds is None


def test_03_states_and_lateness() -> None:
    def state(**kw: Any) -> CronJobState:
        base: dict[str, Any] = {
            "enabled": True,
            "last_status": CronRunStatus.SUCCESS,
            "running_since": None,
            "late": False,
            "now": T0,
        }
        return job_state(**{**base, **kw})

    assert state() is CronJobState.OK
    assert state(enabled=False) is CronJobState.OFF
    assert state(last_status=None) is CronJobState.NEVER
    assert state(last_status=CronRunStatus.FAILED) is CronJobState.FAILING
    assert state(last_status=CronRunStatus.PARTIAL) is CronJobState.PARTIAL
    assert state(late=True) is CronJobState.LATE
    assert state(running_since=T0 - timedelta(minutes=2)) is CronJobState.RUNNING
    # A "running" mark older than the hard time limit is a dead worker.
    assert state(running_since=T0 - timedelta(hours=1)) is CronJobState.OK
    assert not is_late(last_started_at=None, expected_at=None, now=T0)
    due = T0 + timedelta(minutes=5)
    assert not is_late(last_started_at=T0, expected_at=due, now=due + timedelta(minutes=4))
    assert is_late(last_started_at=T0, expected_at=due, now=due + timedelta(minutes=6))


def test_04_the_schedule_is_read_from_the_beat() -> None:
    assert JOBS[FREQUENT].schedule_label == "mỗi 20 giây"
    assert JOBS[FREQUENT].period_seconds == 20
    assert JOBS["system.periodic_heartbeat"].schedule_label == "mỗi 5 phút"
    assert JOBS[DAILY].schedule_label == "hằng ngày 08:00 · 23:00"
    assert JOBS[DAILY].period_seconds >= 3600
    assert JOBS[FREQUENT].label == "Gửi tin trong hàng đợi"
    assert expected_after(JOBS[FREQUENT], T0, T0) == T0 + timedelta(seconds=20)


def test_05_what_a_task_returned() -> None:
    assert outcome_of(None).status is CronRunStatus.SUCCESS
    assert outcome_of({"sent": 3}).status is CronRunStatus.SUCCESS
    partial = outcome_of({"status": "partial", "message": "3/132 TK lỗi"})
    assert partial.status is CronRunStatus.PARTIAL and partial.error == "3/132 TK lỗi"
    assert outcome_of({"status": "FAILED"}).status is CronRunStatus.FAILED


# --- the recorder ------------------------------------------------------------


async def owner_world(session: AsyncSession, settings: Settings) -> Settings:
    session.add(
        User(
            telegram_user_id=OWNER_CHAT,
            full_name="Chủ sở hữu",
            role=Role.OWNER,
            active=True,
            status=UserStatus.ACTIVE,
            telegram_private_chat_id=OWNER_CHAT,
            private_chat_available=True,
        )
    )
    await session.flush()
    return settings.model_copy(update={"meobot_owner_telegram_id": OWNER_CHAT})


async def fail(
    recorder: CronRecorder, key: str, attempt: int = 1, at: datetime = T0, error: str = "Boom"
) -> Any:
    return await recorder.finish(
        key=key,
        task_id=str(uuid.uuid4()),
        trigger=CronTrigger.SCHEDULE if attempt == 1 else CronTrigger.RETRY,
        attempt=attempt,
        triggered_by=None,
        started_at=at,
        finished_at=at + timedelta(seconds=3),
        outcome=RunOutcome(status=CronRunStatus.FAILED, message=error, error=error),
    )


async def succeed(recorder: CronRecorder, key: str, at: datetime = T0) -> Any:
    return await recorder.finish(
        key=key,
        task_id=str(uuid.uuid4()),
        trigger=CronTrigger.SCHEDULE,
        attempt=1,
        triggered_by=None,
        started_at=at,
        finished_at=at + timedelta(seconds=1),
        outcome=RunOutcome(status=CronRunStatus.SUCCESS),
    )


async def alerts(session: AsyncSession, event: str) -> list[OutboundMessage]:
    rows = (await session.execute(select(OutboundMessage))).scalars().all()
    return [row for row in rows if row.event_type == event]


async def test_06_a_daily_failure_retries_then_tells_the_owner_once(
    session: AsyncSession, settings: Settings
) -> None:
    settings = await owner_world(session, settings)
    recorder = CronRecorder(session, settings, jobs=JOBS)

    first = await fail(recorder, DAILY, attempt=1)
    assert (first.retry_in_seconds, first.next_attempt, first.alerted) == (1800, 2, False)
    second = await fail(recorder, DAILY, attempt=2, at=T0 + timedelta(minutes=30))
    assert (second.retry_in_seconds, second.alerted) == (1800, False)
    assert await alerts(session, "cron_job_failed") == []

    third = await fail(
        recorder, DAILY, attempt=3, at=T0 + timedelta(minutes=60), error="Token hết hạn"
    )
    assert third.retry_in_seconds is None and third.alerted is True
    [message] = await alerts(session, "cron_job_failed")
    assert message.telegram_chat_id == OWNER_CHAT and message.is_alert
    assert message.safe_payload_json["job_label"] == "Đồng bộ chi phí Meta"
    assert "đã tự thử lại 2 lần" in message.safe_payload_json["problem"]
    assert message.safe_payload_json["detail"] == "Token hết hạn"
    inbox = (await session.execute(select(UserNotification))).scalars().all()
    assert [row.event_type for row in inbox] == ["cron_job_failed"]
    assert inbox[0].target_kind == "cron_job"

    job = await session.get(CronJob, DAILY)
    assert job is not None and job.consecutive_failures == 3
    assert job.last_status is CronRunStatus.FAILED and job.last_message == "Token hết hạn"
    incident = await session.get(CronJobIncident, job.open_incident_id)
    assert incident is not None and incident.failures == 3 and incident.alerted_at is not None

    # The scheduled run of the next day fails too: same incident, no second message.
    again = await fail(recorder, DAILY, attempt=3, at=T0 + timedelta(days=1))
    assert again.alerted is False
    assert len(await alerts(session, "cron_job_failed")) == 1

    # A good run closes it quietly: only failures are sent (28/10).
    ok = await succeed(recorder, DAILY, at=T0 + timedelta(days=1, hours=1))
    assert ok.recovered is True
    assert await alerts(session, "cron_job_recovered") == []
    await session.refresh(incident)
    assert incident.closed_at is not None and incident.recovery_alerted_at is None
    assert job.open_incident_id is None and job.consecutive_failures == 0
    # Every run of a daily job is kept.
    runs = (await session.execute(select(CronJobRun))).scalars().all()
    assert len(runs) == 5


async def test_07_a_frequent_job_reports_after_five_failures_and_keeps_few_rows(
    session: AsyncSession, settings: Settings
) -> None:
    settings = await owner_world(session, settings)
    recorder = CronRecorder(session, settings, jobs=JOBS)
    # Only Phòng Ads' Meta pulls alert by default; the OWNER switches others on.
    assert (await recorder.ensure(FREQUENT)).notify is False
    (await recorder.ensure(FREQUENT)).notify = True

    for _ in range(3):
        await succeed(recorder, FREQUENT)
    # Its successes stay in the summary only.
    assert (await session.execute(select(CronJobRun))).scalars().all() == []

    results = [await fail(recorder, FREQUENT) for _ in range(6)]
    assert [result.alerted for result in results] == [False, False, False, False, True, False]
    assert all(result.retry_in_seconds is None for result in results)
    assert len(await alerts(session, "cron_job_failed")) == 1
    assert len((await session.execute(select(CronJobRun))).scalars().all()) == 6

    # A job with alerts off: its incidents stay on the screen, nobody is told.
    other = "system.periodic_heartbeat"
    for _ in range(6):
        await fail(recorder, other)
    assert len(await alerts(session, "cron_job_failed")) == 1
    heartbeat = await session.get(CronJob, other)
    assert heartbeat is not None and heartbeat.open_incident_id is not None
    assert (await succeed(recorder, other)).recovered is False
    assert await alerts(session, "cron_job_recovered") == []


async def test_08_a_silent_job_is_late_and_reported_once(
    session: AsyncSession, settings: Settings
) -> None:
    settings = await owner_world(session, settings)
    recorder = CronRecorder(session, settings, jobs=JOBS)
    (await recorder.ensure("system.periodic_heartbeat")).notify = True
    await succeed(recorder, "system.periodic_heartbeat", at=T0)

    assert await recorder.check_late(now=T0 + timedelta(minutes=8)) == 0
    assert await recorder.check_late(now=T0 + timedelta(minutes=11)) == 1
    [message] = await alerts(session, "cron_job_failed")
    assert "Không chạy đúng lịch" in message.safe_payload_json["problem"]
    # Still silent: no second report.
    assert await recorder.check_late(now=T0 + timedelta(minutes=30)) == 0
    jobs = await recorder.jobs()
    assert recorder.late_keys(jobs, T0 + timedelta(minutes=30)) == {"system.periodic_heartbeat"}
    # It runs again: closed, without a message.
    assert (
        await succeed(recorder, "system.periodic_heartbeat", at=T0 + timedelta(minutes=31))
    ).recovered
    assert await alerts(session, "cron_job_recovered") == []


async def test_09_switched_off_records_nothing(session: AsyncSession, settings: Settings) -> None:
    settings = await owner_world(session, settings)
    recorder = CronRecorder(session, settings, jobs=JOBS)
    job = await recorder.ensure(DAILY)
    job.enabled = False
    for attempt in (1, 2, 3):
        await fail(recorder, DAILY, attempt=attempt)
    assert await alerts(session, "cron_job_failed") == []
    assert job.last_status is None


# --- the screen and "Chạy lại" ----------------------------------------------


class FakeSender:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

    def __call__(
        self,
        name: str,
        args: tuple[Any, ...],
        kwargs: Any,
        headers: dict[str, Any],
        options: dict[str, Any],
    ) -> str:
        self.sent.append((name, headers, options))
        return f"task-{len(self.sent)}"


def use_service(world: World, sender: FakeSender) -> None:
    jobs = {**JOBS, **{job.key: job for job in scheduled_jobs(BEAT)}}
    world.client.app.dependency_overrides[get_cron_service] = lambda: CronMonitorService(  # type: ignore[attr-defined]
        world.session, world.settings, jobs=jobs, sender=sender
    )


async def test_10_owner_and_admin_see_the_jobs_others_do_not(world: World) -> None:
    sender = FakeSender()
    use_service(world, sender)
    world.act_as(world.owner)
    page = world.client.get("/api/cron").json()
    assert page["can_manage"] is True
    by_key = {job["key"]: job for job in page["jobs"]}
    assert set(by_key) >= set(JOBS)
    assert by_key[DAILY]["schedule_label"] == "hằng ngày 08:00 · 23:00"
    assert by_key[DAILY]["state"] == "NEVER" and by_key[DAILY]["retries"] == 2
    assert by_key[DAILY]["retry_delay_minutes"] == 30
    assert by_key[FREQUENT]["alert_after"] == 5
    assert page["summary"]["total"] == len(page["jobs"])

    admin = User(full_name="Quản trị", role=Role.ADMIN)
    world.session.add(admin)
    await world.session.flush()
    world.act_as(admin)
    assert world.client.get("/api/cron").json()["can_manage"] is False
    response = world.client.patch(f"/api/cron/jobs/{DAILY}", json={"retries": 1})
    assert response.status_code == 403

    world.act_as(world.member)
    response = world.client.get("/api/cron")
    assert response.status_code == 403 and response.json()["code"] == "cron_forbidden"


async def test_11_chay_lai_queues_once_and_refuses_while_running(world: World) -> None:
    sender = FakeSender()
    use_service(world, sender)
    world.act_as(world.owner)
    response = world.client.post(f"/api/cron/jobs/{DAILY}/run")
    assert response.status_code == 202, response.json()
    assert response.json()["job"]["state"] == "RUNNING"
    [(name, headers, _options)] = sender.sent
    assert name == DAILY and headers[HEADER_TRIGGER] == "MANUAL"
    assert headers[HEADER_USER] == str(world.owner.id)

    again = world.client.post(f"/api/cron/jobs/{DAILY}/run")
    assert again.status_code == 409
    assert again.json()["code"] == "cron_job_running"
    assert len(sender.sent) == 1

    assert world.client.post("/api/cron/jobs/khong.co/run").status_code == 404

    # The OWNER changes the policy; it then sticks.
    response = world.client.patch(
        f"/api/cron/jobs/{DAILY}", json={"retries": 1, "retry_delay_minutes": 15, "alert_after": 2}
    )
    assert response.status_code == 200
    body = response.json()
    assert (body["retries"], body["retry_delay_minutes"], body["alert_after"]) == (1, 15, 2)
    assert world.client.patch(f"/api/cron/jobs/{DAILY}", json={"retries": 9}).status_code == 422


async def test_12_the_history_of_one_job(world: World) -> None:
    use_service(world, FakeSender())
    recorder = CronRecorder(world.session, world.settings, jobs=JOBS)
    # Now, not T0: a run a day old would read "Trễ" by the real clock.
    await fail(recorder, DAILY, at=utcnow() - timedelta(minutes=1))
    world.act_as(world.owner)
    body = world.client.get(f"/api/cron/jobs/{DAILY}").json()
    assert body["job"]["state"] == "FAILING" and body["job"]["consecutive_failures"] == 1
    [run] = body["runs"]
    assert run["status"] == "FAILED" and run["trigger_label"] == "Theo lịch"
    assert run["error"] == "Boom"
    assert body["incidents"][0]["closed_at"] is None


def test_13_the_telegram_button_works_only_in_the_chat_it_was_sent_to(settings: Settings) -> None:
    incident = uuid.uuid4()
    keyboard = rerun_keyboard(incident_id=incident, chat_id=OWNER_CHAT, settings=settings)
    data = keyboard.inline_keyboard[0][0].callback_data
    assert data is not None and len(data.encode()) <= 64
    payload = parse(data, secret=settings.callback_secret, binding=rerun_binding(OWNER_CHAT))
    assert payload is not None and payload.entity_id == incident
    assert payload.action == "cron.rerun"
    forwarded = MemberBinding(bot_id=0, telegram_user_id=1, chat_id=1)
    assert parse(data, secret=settings.callback_secret, binding=forwarded) is None


# --- per-job alerts and recipients (0080) ------------------------------------


async def test_14_the_owner_and_the_jobs_recipients_are_told_of_a_failure(
    session: AsyncSession, settings: Settings
) -> None:
    settings = await owner_world(session, settings)
    await ensure_units(session)

    def person(name: str, chat: int, **over: Any) -> User:
        return User(
            telegram_user_id=chat,
            full_name=name,
            role=Role.EMPLOYEE,
            active=True,
            status=UserStatus.ACTIVE,
            telegram_private_chat_id=chat,
            private_chat_available=True,
            **over,
        )

    head = person("Trưởng phòng Ads", 930_002)
    extra = person("Kế toán", 930_003)
    gone = person("Đã nghỉ", 930_004, deleted_at=T0)
    session.add_all([head, extra, gone])
    await session.flush()
    await place(session, UnitCode.ADS, head.id, UnitMemberRole.HEAD)
    recorder = CronRecorder(session, settings, jobs=JOBS)

    # A Meta pull starts with alerts on and the ADS heads as recipients.
    job = await recorder.ensure(DAILY)
    assert job.notify is True and job.notify_user_ids == [str(head.id)]
    job.notify_user_ids = [str(head.id), str(extra.id), str(gone.id)]

    for attempt in (1, 2, 3):
        await fail(recorder, DAILY, attempt=attempt, at=T0 + timedelta(minutes=30 * attempt))
    messages = {row.telegram_chat_id: row for row in await alerts(session, "cron_job_failed")}
    # A deleted account is skipped; the OWNER's copy alone offers "Chạy lại".
    assert set(messages) == {OWNER_CHAT, 930_002, 930_003}
    assert messages[OWNER_CHAT].safe_payload_json["can_rerun"] == "1"
    assert messages[930_002].safe_payload_json["can_rerun"] == "0"
    inbox = (await session.execute(select(UserNotification))).scalars().all()
    assert {row.recipient_user_id for row in inbox} >= {head.id, extra.id}
    assert gone.id not in {row.recipient_user_id for row in inbox}

    from meobot.application.outbound_keyboards import _cron_rerun_markup
    from meobot.domain.notifications.templates import render

    assert _cron_rerun_markup(settings, messages[930_002]) is None
    assert _cron_rerun_markup(settings, messages[OWNER_CHAT]) is not None
    text = render("cron.job_failed", messages[930_002].safe_payload_json)
    assert "Chủ sở hữu đã được báo" in text and "Bấm" not in text

    # Alerts off: the next incident is not sent to anybody.
    await succeed(recorder, DAILY, at=T0 + timedelta(hours=3))
    job.notify = False
    for attempt in (1, 2, 3):
        await fail(recorder, DAILY, attempt=attempt, at=T0 + timedelta(hours=4, minutes=attempt))
    assert len(await alerts(session, "cron_job_failed")) == 3


async def test_15_the_owner_edits_a_jobs_alerts_on_the_screen(world: World) -> None:
    use_service(world, FakeSender())
    extra = User(full_name="Người nhận", role=Role.EMPLOYEE, status=UserStatus.ACTIVE)
    gone = User(full_name="Đã xoá", role=Role.EMPLOYEE, status=UserStatus.ACTIVE, deleted_at=T0)
    world.session.add_all([extra, gone])
    await world.session.flush()
    world.act_as(world.owner)
    page = world.client.get("/api/cron").json()
    by_key = {job["key"]: job for job in page["jobs"]}
    assert by_key[DAILY]["notify"] is True and by_key[FREQUENT]["notify"] is False
    people = {person["name"] for person in page["people"]}
    assert "Người nhận" in people and "Đã xoá" not in people

    body = world.client.patch(
        f"/api/cron/jobs/{FREQUENT}",
        json={"notify": True, "notify_user_ids": [str(extra.id), str(extra.id)]},
    ).json()
    assert body["notify"] is True
    assert [person["name"] for person in body["recipients"]] == ["Người nhận"]
    # Empty = the OWNER alone.
    cleared = world.client.patch(f"/api/cron/jobs/{FREQUENT}", json={"notify_user_ids": []})
    assert cleared.json()["recipients"] == [] and cleared.json()["notify"] is True
    bad = world.client.patch(f"/api/cron/jobs/{FREQUENT}", json={"notify_user_ids": [str(gone.id)]})
    assert bad.status_code == 422 and bad.json()["details"]["field"] == "notify_user_ids"

    admin = User(full_name="Quản trị", role=Role.ADMIN, status=UserStatus.ACTIVE)
    world.session.add(admin)
    await world.session.flush()
    world.act_as(admin)
    assert world.client.get("/api/cron").json()["people"] == []
    denied = world.client.patch(f"/api/cron/jobs/{FREQUENT}", json={"notify": False})
    assert denied.status_code == 403
