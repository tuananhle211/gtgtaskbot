"""The job monitor ("Tác vụ định kỳ"): ``cron_jobs``, ``cron_job_runs``, ``cron_job_incidents``.

Revision ID: 0077
Revises: 0076
Create Date: 2026-10-27

Every periodic Celery task is recorded: how its last run went, a history of
runs (every run of a slow job, the failures of a frequent one) and incidents -
a run of failures, reported to the OWNER once, retried by the job's policy and
closed by the next success. See ``docs/ops/CRON_MONITOR_PLAN.md``.

Empty tables change nothing; the rows appear as the jobs run.

Downgrade
---------
Drops the three tables (the history is lost).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0077"
down_revision: str | None = "0076"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JOBS = "cron_jobs"
RUNS = "cron_job_runs"
INCIDENTS = "cron_job_incidents"
_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    op.create_table(
        JOBS,
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("retries", sa.Integer(), nullable=False),
        sa.Column("retry_delay_seconds", sa.Integer(), nullable=False),
        sa.Column("alert_after", sa.Integer(), nullable=False),
        sa.Column("keep_successes", sa.Boolean(), nullable=False),
        sa.Column("policy_custom", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("last_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status", sa.String(length=10), nullable=True),
        sa.Column("last_message", sa.String(length=300), nullable=True),
        sa.Column("last_duration_ms", sa.Integer(), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "consecutive_failures", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("running_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("running_task_id", sa.String(length=64), nullable=True),
        sa.Column("open_incident_id", sa.Uuid(), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_cron_jobs")),
    )
    op.create_table(
        RUNS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_key", sa.String(length=120), nullable=False),
        sa.Column("task_id", sa.String(length=64), nullable=True),
        sa.Column("trigger", sa.String(length=10), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("triggered_by_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("message", sa.String(length=500), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("result", _JSON, nullable=True),
        sa.ForeignKeyConstraint(
            ["job_key"],
            [f"{JOBS}.key"],
            name=op.f("fk_cron_job_runs_job_key_cron_jobs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["triggered_by_user_id"],
            ["users.id"],
            name=op.f("fk_cron_job_runs_triggered_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cron_job_runs")),
    )
    op.create_index("ix_cron_job_runs_job_started", RUNS, ["job_key", "started_at"])
    op.create_table(
        INCIDENTS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_key", sa.String(length=120), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column(
            "opened_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(length=300), nullable=True),
        sa.Column("alerted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recovery_alerted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rerun_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rerun_requested_by_user_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["job_key"],
            [f"{JOBS}.key"],
            name=op.f("fk_cron_job_incidents_job_key_cron_jobs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["rerun_requested_by_user_id"],
            ["users.id"],
            name=op.f("fk_cron_job_incidents_rerun_requested_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cron_job_incidents")),
    )
    op.create_index("ix_cron_job_incidents_job_opened", INCIDENTS, ["job_key", "opened_at"])


def downgrade() -> None:
    op.drop_index("ix_cron_job_incidents_job_opened", table_name=INCIDENTS)
    op.drop_table(INCIDENTS)
    op.drop_index("ix_cron_job_runs_job_started", table_name=RUNS)
    op.drop_table(RUNS)
    op.drop_table(JOBS)
