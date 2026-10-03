"""Celery tasks in eager mode - no broker, no worker."""

from __future__ import annotations

import pytest

from meobot.tasks.celery_app import (
    QUEUE_DEFAULT,
    QUEUE_INTEGRATIONS,
    QUEUE_NOTIFICATIONS,
    QUEUE_REPORTS,
    celery_app,
)
from meobot.tasks.reports import generate_demo
from meobot.tasks.system import periodic_heartbeat, ping


def test_eager_mode_is_enabled_in_tests() -> None:
    """CELERY_TASK_ALWAYS_EAGER from the test environment reaches the app."""
    assert celery_app.conf.task_always_eager is True


def test_queues_are_declared() -> None:
    declared = {queue.name for queue in celery_app.conf.task_queues}
    assert declared == {QUEUE_DEFAULT, QUEUE_INTEGRATIONS, QUEUE_REPORTS, QUEUE_NOTIFICATIONS}


def test_beat_schedule_contains_the_heartbeat() -> None:
    schedule = celery_app.conf.beat_schedule
    assert "system-periodic-heartbeat" in schedule
    assert schedule["system-periodic-heartbeat"]["task"] == "system.periodic_heartbeat"


def test_ping_runs_eagerly() -> None:
    result = ping.delay()
    assert result.successful()
    assert result.get()["pong"] is True


def test_heartbeat_runs_eagerly() -> None:
    payload = periodic_heartbeat.apply().get()
    assert "heartbeat_at" in payload


def test_demo_report_accepts_known_periods() -> None:
    payload = generate_demo.apply(kwargs={"period": "weekly"}).get()
    assert payload["period"] == "weekly"
    assert payload["rows"] == 0


def test_demo_report_rejects_unknown_period() -> None:
    """A bad argument fails immediately instead of being retried forever."""
    with pytest.raises(ValueError, match="Unsupported report period"):
        generate_demo.apply(kwargs={"period": "hourly"}).get()


def test_tasks_are_routed_to_their_queues() -> None:
    routes = celery_app.conf.task_routes
    assert routes["reports.*"]["queue"] == QUEUE_REPORTS
    assert routes["system.*"]["queue"] == QUEUE_DEFAULT
    assert routes["integrations.*"]["queue"] == QUEUE_INTEGRATIONS


def test_retry_policy_is_configured_on_the_report_task() -> None:
    assert generate_demo.max_retries == 3
    assert generate_demo.retry_backoff is True


# --- Milestone 2 tasks ------------------------------------------------------
def test_worker_imports_the_milestone_2_task_modules() -> None:
    """A module missing from `include` is a task the worker silently never has."""
    assert {
        "meobot.tasks.sheets",
        "meobot.tasks.scripts",
    } <= set(celery_app.conf.include)


def test_milestone_2_tasks_are_registered() -> None:
    """A task nobody registered is a task the bot silently never runs.

    The worker registers them by importing the modules listed in `include`;
    this does the same thing explicitly.
    """
    celery_app.loader.import_default_modules()
    registered = set(celery_app.tasks)
    assert {
        "sheets.sync_profile",
        "sheets.sync_all_active_profiles",
        "scripts.review_script",
        "scripts.review_pending",
        "scripts.push_decision_to_sheet",
        "conversations.cleanup_expired",
    } <= registered


def test_google_work_is_isolated_on_the_integrations_queue() -> None:
    """A Sheets outage must not starve reviews or heartbeats."""
    routes = celery_app.conf.task_routes
    assert routes["sheets.*"]["queue"] == QUEUE_INTEGRATIONS
    assert routes["scripts.push_decision_to_sheet"]["queue"] == QUEUE_INTEGRATIONS
    assert routes["scripts.*"]["queue"] == QUEUE_DEFAULT
    assert routes["conversations.*"]["queue"] == QUEUE_DEFAULT


def test_beat_schedules_the_milestone_2_jobs() -> None:
    schedule = celery_app.conf.beat_schedule
    assert schedule["sheets-sync-all-active-profiles"]["task"] == "sheets.sync_all_active_profiles"
    assert schedule["scripts-review-pending"]["task"] == "scripts.review_pending"
    assert schedule["conversations-cleanup-expired"]["task"] == "conversations.cleanup_expired"


def test_sheet_sync_expires_before_the_next_run() -> None:
    """A backed-up queue must never stack two syncs of the same sheets."""
    entry = celery_app.conf.beat_schedule["sheets-sync-all-active-profiles"]
    assert entry["options"]["expires"] < entry["schedule"]
