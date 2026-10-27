"""The vocabulary and the rules of the job monitor (0077).

Every periodic Celery task is a **job**. Each run ends ``SUCCESS``,
``PARTIAL`` (it finished but said part of it failed) or ``FAILED``; a job that
has not run when it should have is **late**. A run of failures is an
**incident**: it is reported once, retried by the job's policy, and closed by
the next success - which is reported too.

Pure: no database, no Celery, so the rules are tested on their own.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class CronRunStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class CronTrigger(StrEnum):
    """Who started a run."""

    SCHEDULE = "SCHEDULE"
    #: The job's own policy, after a failure.
    RETRY = "RETRY"
    #: "Chạy lại" on the screen or in Telegram.
    MANUAL = "MANUAL"


class CronIncidentKind(StrEnum):
    FAILED = "FAILED"
    #: Did not run when it should have (the beat or the workers are down).
    LATE = "LATE"


class CronJobState(StrEnum):
    """What the screen shows for a job, from its summary row."""

    OK = "OK"
    FAILING = "FAILING"
    PARTIAL = "PARTIAL"
    LATE = "LATE"
    RUNNING = "RUNNING"
    #: Never run since the monitor started.
    NEVER = "NEVER"
    #: Monitoring switched off.
    OFF = "OFF"


RUN_STATUS_LABELS: dict[CronRunStatus, str] = {
    CronRunStatus.RUNNING: "Đang chạy",
    CronRunStatus.SUCCESS: "Thành công",
    CronRunStatus.PARTIAL: "Lỗi một phần",
    CronRunStatus.FAILED: "Lỗi",
}

TRIGGER_LABELS: dict[CronTrigger, str] = {
    CronTrigger.SCHEDULE: "Theo lịch",
    CronTrigger.RETRY: "Tự thử lại",
    CronTrigger.MANUAL: "Chạy tay",
}

STATE_LABELS: dict[CronJobState, str] = {
    CronJobState.OK: "Ổn",
    CronJobState.FAILING: "Đang lỗi",
    CronJobState.PARTIAL: "Lỗi một phần",
    CronJobState.LATE: "Trễ",
    CronJobState.RUNNING: "Đang chạy",
    CronJobState.NEVER: "Chưa chạy",
    CronJobState.OFF: "Tắt theo dõi",
}

#: A job that runs at least this often is "frequent": no retries of its own
#: (the next run comes soon) and no alert for a single blip.
FREQUENT_SECONDS = 3600
#: How long a run may say ``RUNNING`` before it is presumed dead - Celery's
#: hard time limit (``task_time_limit``) plus a margin.
RUNNING_STALE_SECONDS = 15 * 60
#: Extra slack before a job counts as late, on top of one full period.
LATE_GRACE_SECONDS = 5 * 60


@dataclass(frozen=True, slots=True)
class CronPolicy:
    #: Automatic retries after a failed run, each ``retry_delay_seconds`` later.
    retries: int
    retry_delay_seconds: int
    #: Report once this many runs in a row have failed (and no retry is left).
    alert_after: int
    #: Keep a history row for every successful run (else only the summary).
    keep_successes: bool


def default_policy(period_seconds: int) -> CronPolicy:
    """Chốt 27/10: a daily-ish job retries twice, 30 minutes apart, then
    reports; a frequent one never retries and reports after 5 failures."""
    if period_seconds < FREQUENT_SECONDS:
        return CronPolicy(retries=0, retry_delay_seconds=0, alert_after=5, keep_successes=False)
    return CronPolicy(retries=2, retry_delay_seconds=1800, alert_after=1, keep_successes=True)


@dataclass(frozen=True, slots=True)
class FailureDecision:
    """What to do after a failed run."""

    #: Queue the job again after this many seconds (``None``: no retry).
    retry_in_seconds: int | None
    #: The attempt number the retry will carry.
    next_attempt: int
    #: Report the incident now.
    alert: bool


def after_failure(*, policy: CronPolicy, attempt: int, consecutive: int) -> FailureDecision:
    """``attempt`` = 1 for the scheduled run, 2.. for its retries;
    ``consecutive`` = failed runs in a row, this one included.

    A retry is queued while the policy has one left for this chain. The
    incident is reported once there is nothing left to try and enough runs
    have failed - the caller reports it only if it has not already.
    """
    if attempt <= policy.retries:
        return FailureDecision(
            retry_in_seconds=policy.retry_delay_seconds, next_attempt=attempt + 1, alert=False
        )
    return FailureDecision(
        retry_in_seconds=None,
        next_attempt=attempt,
        alert=consecutive >= max(policy.alert_after, 1),
    )


def is_late(
    *,
    last_started_at: datetime | None,
    expected_at: datetime | None,
    now: datetime,
) -> bool:
    """Late = the run that was due at ``expected_at`` has not started, past
    the grace. ``expected_at`` is the scheduler's next due time after the last
    run; a job that never ran is not late (the monitor may be newer)."""
    if last_started_at is None or expected_at is None:
        return False
    return now > expected_at + timedelta(seconds=LATE_GRACE_SECONDS)


def job_state(
    *,
    enabled: bool,
    last_status: CronRunStatus | None,
    running_since: datetime | None,
    late: bool,
    now: datetime,
) -> CronJobState:
    if not enabled:
        return CronJobState.OFF
    if running_since is not None and now - running_since < timedelta(seconds=RUNNING_STALE_SECONDS):
        return CronJobState.RUNNING
    if late:
        return CronJobState.LATE
    if last_status is None:
        return CronJobState.NEVER
    if last_status is CronRunStatus.FAILED:
        return CronJobState.FAILING
    if last_status is CronRunStatus.PARTIAL:
        return CronJobState.PARTIAL
    return CronJobState.OK


def short_error(text: str | None, limit: int = 300) -> str:
    """One line, bounded - for the summary and the Telegram alert."""
    flat = " ".join((text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


__all__ = [
    "FREQUENT_SECONDS",
    "LATE_GRACE_SECONDS",
    "RUNNING_STALE_SECONDS",
    "RUN_STATUS_LABELS",
    "STATE_LABELS",
    "TRIGGER_LABELS",
    "CronIncidentKind",
    "CronJobState",
    "CronPolicy",
    "CronRunStatus",
    "CronTrigger",
    "FailureDecision",
    "after_failure",
    "default_policy",
    "is_late",
    "job_state",
    "short_error",
]
