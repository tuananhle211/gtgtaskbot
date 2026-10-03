"""Celery application, queues, retry defaults and the beat schedule.

Queues:

* ``q_default`` - short internal work (heartbeats, reminders).
* ``q_integrations`` - anything that talks to a third party; kept separate so a
  Google or Meta outage cannot starve the rest of the system.
* ``q_reports`` - long report generation.
* ``q_notifications`` - the transactional outbox drain: cross-chat Telegram
  delivery, retries and stale-claim recovery.

Every task carries a ``request_id`` header so its log lines correlate with the
Telegram message or API call that triggered it.
"""

from __future__ import annotations

from typing import Any

from celery import Celery, Task
from celery.signals import setup_logging, task_prerun
from kombu import Queue

from meobot.core.config import get_settings
from meobot.core.context import new_request_id, set_request_id
from meobot.core.logging import configure_logging, get_logger

logger = get_logger(__name__)

QUEUE_DEFAULT = "q_default"
QUEUE_INTEGRATIONS = "q_integrations"
QUEUE_REPORTS = "q_reports"
#: Outbound Telegram delivery. Its own queue so a Telegram outage cannot starve
#: sheet syncs or reviews, and so delivery can be scaled independently.
QUEUE_NOTIFICATIONS = "q_notifications"

#: Default retry policy for tasks that talk to flaky things.
RETRY_KWARGS: dict[str, Any] = {
    "max_retries": 3,
    "countdown": 5,
}

settings = get_settings()

celery_app = Celery(
    "meobot",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "meobot.tasks.system",
        "meobot.tasks.reports",
        "meobot.tasks.sheets",
        "meobot.tasks.scripts",
        "meobot.tasks.drive",
        "meobot.tasks.notifications",
        "meobot.tasks.reminders",
        "meobot.tasks.guest_replay",
        "meobot.tasks.destination_health",
        "meobot.tasks.pr_reviews",
        "meobot.tasks.pr_content_work",
        "meobot.tasks.pr_channel_sync",
        "meobot.tasks.pr_work_recurring",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # The database stores UTC; Celery schedules in UTC too. Display conversion
    # happens at the Telegram boundary only.
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_track_started=True,
    task_time_limit=600,
    task_soft_time_limit=540,
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=200,
    result_expires=3600,
    task_always_eager=settings.celery_task_always_eager,
    task_eager_propagates=settings.celery_task_always_eager,
    task_default_queue=QUEUE_DEFAULT,
    task_queues=(
        Queue(QUEUE_DEFAULT),
        Queue(QUEUE_INTEGRATIONS),
        Queue(QUEUE_REPORTS),
        Queue(QUEUE_NOTIFICATIONS),
    ),
    task_routes={
        "system.*": {"queue": QUEUE_DEFAULT},
        "reports.*": {"queue": QUEUE_REPORTS},
        "integrations.*": {"queue": QUEUE_INTEGRATIONS},
        # Anything touching Google shares the integrations queue, so a Sheets
        # or Drive outage cannot block reviews or heartbeats.
        "sheets.*": {"queue": QUEUE_INTEGRATIONS},
        "drive.*": {"queue": QUEUE_INTEGRATIONS},
        "scripts.push_decision_to_sheet": {"queue": QUEUE_INTEGRATIONS},
        "scripts.*": {"queue": QUEUE_DEFAULT},
        "conversations.*": {"queue": QUEUE_DEFAULT},
        "notifications.*": {"queue": QUEUE_NOTIFICATIONS},
        # PR AI review is a third-party call, so it shares the queue that
        # exists to keep a slow provider from starving everything else.
        "pr.*": {"queue": QUEUE_INTEGRATIONS},
        # Reminders end in an outbound message, so they share the delivery
        # queue rather than competing with sheet syncs for a worker slot.
        "reminders.*": {"queue": QUEUE_NOTIFICATIONS},
    },
    beat_schedule={
        "system-periodic-heartbeat": {
            "task": "system.periodic_heartbeat",
            "schedule": 300.0,
            "options": {"queue": QUEUE_DEFAULT, "expires": 240},
        },
        "sheets-sync-all-active-profiles": {
            "task": "sheets.sync_all_active_profiles",
            "schedule": float(settings.sheet_sync_interval_seconds),
            # Expire before the next run so a backed-up queue never stacks
            # two syncs of the same sheets.
            "options": {
                "queue": QUEUE_INTEGRATIONS,
                "expires": settings.sheet_sync_interval_seconds - 60,
            },
        },
        "scripts-review-pending": {
            "task": "scripts.review_pending",
            "schedule": 900.0,
            "options": {"queue": QUEUE_DEFAULT, "expires": 600},
        },
        # Drains the outbox. Frequent and cheap: it claims a bounded batch and
        # returns immediately when there is nothing due.
        "notifications-drain-outbox": {
            "task": "notifications.drain_outbox",
            "schedule": 20.0,
            "options": {"queue": QUEUE_NOTIFICATIONS, "expires": 15},
        },
        # Returns messages stranded by a worker that died mid-delivery.
        # M3. Frequent and cheap, like the outbox drain above: it claims a
        # bounded batch and returns immediately when nothing is waiting. The
        # interval is what a person waits before automatically-recorded work
        # appears on their board.
        "pr-sweep-content-work": {
            "task": "pr.sweep_content_work",
            "schedule": 30.0,
            "options": {"queue": QUEUE_DEFAULT, "expires": 25},
        },
        # Returns projections stranded by a worker that died mid-run.
        "pr-recover-stale-content-work": {
            "task": "pr.recover_stale_content_work",
            "schedule": 600.0,
            "options": {"queue": QUEUE_DEFAULT, "expires": 480},
        },
        "notifications-recover-stale": {
            "task": "notifications.recover_stale",
            "schedule": 300.0,
            "options": {"queue": QUEUE_NOTIFICATIONS, "expires": 240},
        },
        "conversations-cleanup-expired": {
            "task": "conversations.cleanup_expired",
            "schedule": 3600.0,
            "options": {"queue": QUEUE_DEFAULT, "expires": 1800},
        },
        # Settles creation records whose worker died between "Drive said yes"
        # and the commit. Finds files; never creates them.
        "drive-reconcile-created-spreadsheets": {
            "task": "drive.reconcile_created_spreadsheets",
            "schedule": 1800.0,
            "options": {"queue": QUEUE_INTEGRATIONS, "expires": 1500},
        },
        # --- 0.6.0a2 ------------------------------------------------------
        # Fires due reminders. Frequent because a reminder that arrives 40
        # seconds late is fine and one that arrives 15 minutes late is not.
        # Safe to run twice: everything it inserts is protected by
        # ``uq_reminder_occurrences_reminder_moment``.
        "reminders-sweep-due": {
            "task": "reminders.sweep_due",
            "schedule": float(settings.reminder_sweep_interval_seconds),
            # Expires before the next run so a backed-up queue never stacks
            # two sweeps of the same reminders.
            "options": {
                "queue": QUEUE_NOTIFICATIONS,
                "expires": max(10, settings.reminder_sweep_interval_seconds - 10),
            },
        },
        # --- Step 1F.2.4b -------------------------------------------------
        # Finds channel connections that are due and dispatches one sync each.
        # Hourly, while the *cadence* is daily: the sweep is one indexed query
        # that usually returns nothing, and running it often means a channel
        # connected at 14:00 is picked up within the hour rather than tomorrow.
        "pr-sweep-channel-syncs": {
            "task": "pr.sweep_channel_syncs",
            "schedule": float(settings.pr_channel_sync_sweep_interval_seconds),
            # Expires before the next sweep so a backed-up queue never stacks
            # two sweeps competing for the same claims.
            "options": {
                "queue": QUEUE_INTEGRATIONS,
                "expires": max(60, settings.pr_channel_sync_sweep_interval_seconds - 60),
            },
        },
        # --- M4B ------------------------------------------------------------
        # Walks active recurring templates forward and files the work they owe.
        # Every five minutes, while templates fire at most daily: the sweep is
        # one indexed query that usually returns nothing, and the interval is
        # what decides how soon after 09:00 a routine job actually appears on
        # somebody's board. There is no "release stale" companion, because
        # nothing is claimed - two unique indexes do the work a claim would.
        "pr-sweep-recurring-work": {
            "task": "pr.sweep_recurring_work",
            "schedule": float(settings.pr_recurring_sweep_interval_seconds),
            # Expires before the next sweep so a backed-up queue never stacks
            # two sweeps of the same templates.
            "options": {
                "queue": QUEUE_DEFAULT,
                "expires": max(60, settings.pr_recurring_sweep_interval_seconds - 30),
            },
        },
        # Returns connections stranded SYNCING by a worker that died. Without
        # it, one crash silently stops a channel syncing for ever.
        "pr-release-stale-channel-syncs": {
            "task": "pr.release_stale_channel_syncs",
            "schedule": 1800.0,
            "options": {"queue": QUEUE_INTEGRATIONS, "expires": 1500},
        },
        # Reads chat state; never sends a visible test message.
        "notifications-sweep-destination-health": {
            "task": "notifications.sweep_destination_health",
            "schedule": float(settings.notification_destination_health_interval_seconds),
            "options": {
                "queue": QUEUE_NOTIFICATIONS,
                "expires": max(60, settings.notification_destination_health_interval_seconds - 60),
            },
        },
        # Blanks the text of held Guest questions nobody decided about in time.
        # --- Step 1F ------------------------------------------------------
        # Dispatches committed AI review runs. Frequent and cheap: it claims a
        # bounded batch and returns immediately when there is nothing queued.
        # This interval is the delay between content entering AI_REVIEW and the
        # review starting, which the web panel shows as "Đang chờ xử lý…".
        "pr-sweep-ai-review-runs": {
            "task": "pr.sweep_ai_review_runs",
            "schedule": float(settings.pr_ai_review_sweep_interval_seconds),
            "options": {
                "queue": QUEUE_INTEGRATIONS,
                "expires": max(5, settings.pr_ai_review_sweep_interval_seconds - 5),
            },
        },
        # Step 1F.1. Re-reads official policy pages, daily. Creates snapshots
        # when the text changed and changes no ACTIVE pack - activation stays an
        # explicit operator act, because a website edit is not a decision.
        "pr-refresh-policy-sources": {
            "task": "pr.refresh_policy_sources",
            "schedule": float(settings.pr_policy_refresh_interval_seconds),
            "options": {
                "queue": QUEUE_INTEGRATIONS,
                "expires": max(600, settings.pr_policy_refresh_interval_seconds // 2),
            },
        },
        # Returns reviews stranded by a worker that died mid-call.
        "pr-recover-stale-ai-review-runs": {
            "task": "pr.recover_stale_ai_review_runs",
            "schedule": 300.0,
            "options": {"queue": QUEUE_INTEGRATIONS, "expires": 240},
        },
        "notifications-purge-deferred-guest-messages": {
            "task": "notifications.purge_deferred_guest_messages",
            "schedule": 3600.0,
            "options": {"queue": QUEUE_NOTIFICATIONS, "expires": 1800},
        },
    },
)


@setup_logging.connect
def _configure_celery_logging(**_: object) -> None:
    """Replace Celery's logging with MeoBot's structured, redacting setup."""
    configure_logging(
        service="worker",
        level=settings.log_level,
        log_format=settings.log_format,
    )


@task_prerun.connect
def _bind_request_id(task: Task[Any, Any] | None = None, **kwargs: object) -> None:
    """Bind the incoming correlation id (or mint one) for the task's lifetime."""
    headers = getattr(getattr(task, "request", None), "headers", None) or {}
    request_id = headers.get("request_id") if isinstance(headers, dict) else None
    set_request_id(str(request_id) if request_id else new_request_id())
