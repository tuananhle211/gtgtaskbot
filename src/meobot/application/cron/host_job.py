"""Jobs that run on the server itself, not in Celery (plan docs/ops/SCALING_PLAN.md C2).

The daily database dump is a host cron script (``deploy/backup/db-backup-daily.sh``,
installed as ``/root/bin/db-backup-daily.sh``). At its end it reports here:

    docker compose exec -T api python -m meobot.application.cron.host_job \\
        --key host.db_backup --status ok --started 1762800600 --message "..."

so "Tác vụ định kỳ" shows the run and a failure alerts the OWNER at once, like
any other job. These jobs are never re-run from the web (there is no Celery
task behind them): "Chạy lại" answers 422 ``cron_host_job``.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.cron.recorder import CronRecorder, FinishResult, RunOutcome
from meobot.core.config import Settings, get_settings
from meobot.core.time import utcnow
from meobot.db.session import Database
from meobot.domain.cron.models import CronRunStatus, CronTrigger

#: Host jobs this module accepts (labels in ``domain/cron/catalog.py``).
HOST_JOB_KEYS = frozenset({"host.db_backup"})


async def record_host_run(
    session: AsyncSession,
    settings: Settings,
    *,
    key: str,
    ok: bool,
    message: str,
    started_at: datetime,
    finished_at: datetime | None = None,
) -> FinishResult:
    if key not in HOST_JOB_KEYS:
        raise ValueError(f"unknown host job {key!r}")
    recorder = CronRecorder(session, settings, jobs={})
    job = await recorder.ensure(key)
    if not job.policy_custom:
        # Nobody re-runs a host script from here: no retry, tell at the first failure.
        job.retries = 0
        job.retry_delay_seconds = 0
        job.alert_after = 1
    outcome = RunOutcome(
        status=CronRunStatus.SUCCESS if ok else CronRunStatus.FAILED,
        message=message[:300] or None,
        error=None if ok else message[:4000],
    )
    return await recorder.finish(
        key=key,
        task_id=None,
        trigger=CronTrigger.SCHEDULE,
        attempt=1,
        triggered_by=None,
        started_at=started_at,
        finished_at=finished_at or utcnow(),
        outcome=outcome,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report a host job's run to Tác vụ định kỳ.")
    parser.add_argument("--key", required=True, choices=sorted(HOST_JOB_KEYS))
    parser.add_argument("--status", required=True, choices=("ok", "failed"))
    parser.add_argument("--started", type=float, required=True, help="Unix time it started")
    parser.add_argument("--message", default="")
    args = parser.parse_args(argv)

    async def run() -> None:
        settings = get_settings()
        database = Database(settings)
        try:
            async with database.transaction() as session:
                await record_host_run(
                    session,
                    settings,
                    key=args.key,
                    ok=args.status == "ok",
                    message=args.message,
                    started_at=datetime.fromtimestamp(args.started, UTC),
                )
        finally:
            await database.dispose()

    asyncio.run(run())
    return 0


if __name__ == "__main__":  # pragma: no cover - the script's entry point
    raise SystemExit(main())


__all__ = ["HOST_JOB_KEYS", "main", "record_host_run"]
