"""Tài chính's scheduled jobs.

* ``finance.reminders`` - 08:00 Vietnam: what waits on each approver (F8) and
  the paid requests still missing papers (F5).
"""

from __future__ import annotations

from typing import Any

from celery import shared_task

from meobot.application.ads.sync_service import vn_today
from meobot.application.finance.reminders import send_reminders
from meobot.tasks.runtime import TaskContext, run_async


@shared_task(name="finance.reminders")
def reminders() -> dict[str, Any]:
    return run_async(_run)


async def _run(context: TaskContext) -> dict[str, Any]:
    async with context.database.transaction() as session:
        counts = await send_reminders(session, context.settings, vn_today())
    return {
        "status": "SUCCESS",
        "message": (
            f"Nhắc {counts['approvers']} người duyệt, {counts['papers']} hồ sơ thiếu chứng từ."
        ),
        **counts,
    }


__all__ = ["reminders"]
