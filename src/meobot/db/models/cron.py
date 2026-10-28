"""The job monitor ("Tác vụ định kỳ", 0077): jobs, their runs, their incidents.

See :mod:`meobot.domain.cron.models`. ``cron_jobs`` is one row per scheduled
task with its policy and a summary of how it last went; ``cron_job_runs`` the
history (every run of a slow job, only the failures of a frequent one);
``cron_job_incidents`` one row per run of failures, reported once.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from meobot.core.time import utcnow
from meobot.db.base import Base, JSONColumn, TimestampMixin, UUIDPrimaryKeyMixin, value_enum
from meobot.db.models.pr import RESTRICT, USERS_TABLE
from meobot.domain.cron.models import CronIncidentKind, CronRunStatus, CronTrigger

CRON_JOBS = "cron_jobs"
CRON_JOB_RUNS = "cron_job_runs"
CRON_JOB_INCIDENTS = "cron_job_incidents"


class CronJob(Base, TimestampMixin):
    __tablename__ = CRON_JOBS

    #: The Celery task name, e.g. ``notifications.drain_outbox``.
    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    #: Off = no tracking, no alert (the beat still runs it).
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    retries: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retry_delay_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    alert_after: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    keep_successes: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: The policy was edited by hand: a schedule change no longer resets it.
    policy_custom: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    #: Failures are sent (0080): to the OWNER, plus ``notify_user_ids``.
    #: Off = the incident only shows on the screen.
    notify: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    #: Who else gets the alert (user ids as text); empty = the OWNER alone.
    notify_user_ids: Mapped[list[str]] = mapped_column(
        JSONColumn, nullable=False, default=list, server_default=text("'[]'")
    )
    # --- the summary --------------------------------------------------------
    last_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[CronRunStatus | None] = mapped_column(
        value_enum(CronRunStatus, name="cron_run_status", length=10), nullable=True
    )
    last_message: Mapped[str | None] = mapped_column(String(300), nullable=True)
    last_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    #: Set while a run of a slow job (or a manual / retry run) is going.
    running_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    running_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    open_incident_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)


class CronJobRun(Base, UUIDPrimaryKeyMixin):
    __tablename__ = CRON_JOB_RUNS
    __table_args__ = (Index("ix_cron_job_runs_job_started", "job_key", "started_at"),)

    job_key: Mapped[str] = mapped_column(
        ForeignKey(f"{CRON_JOBS}.key", ondelete="CASCADE"), nullable=False
    )
    task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    trigger: Mapped[CronTrigger] = mapped_column(
        value_enum(CronTrigger, name="cron_trigger", length=10), nullable=False
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    triggered_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[CronRunStatus] = mapped_column(
        value_enum(CronRunStatus, name="cron_run_status", length=10), nullable=False
    )
    message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: What the task returned, when it was a small JSON object.
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONColumn, nullable=True)


class CronJobIncident(Base, UUIDPrimaryKeyMixin):
    __tablename__ = CRON_JOB_INCIDENTS
    __table_args__ = (Index("ix_cron_job_incidents_job_opened", "job_key", "opened_at"),)

    job_key: Mapped[str] = mapped_column(
        ForeignKey(f"{CRON_JOBS}.key", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[CronIncidentKind] = mapped_column(
        value_enum(CronIncidentKind, name="cron_incident_kind", length=10), nullable=False
    )
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    alerted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recovery_alerted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rerun_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rerun_requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )


__all__ = [
    "CRON_JOBS",
    "CRON_JOB_INCIDENTS",
    "CRON_JOB_RUNS",
    "CronJob",
    "CronJobIncident",
    "CronJobRun",
]
