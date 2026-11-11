"""Hộp chat CSKH's background work (0102).

* ``cskh.poll_pancake`` - every 20 s: read every connected page from Pancake
  (one run at a time: a Postgres advisory lock; a run still going makes the
  next one return at once).
* ``cskh.refresh_meta`` - every 10 min: Pancake tags / staff, new pages on the
  accounts, POS Cake shops.
* ``cskh.connect_pages`` - sent by "Kết nối page": connect the pages named, the
  progress in ``system_settings`` ``cskh_job``.

The poll never reports FAILED to the job monitor (a page Pancake refuses is
shown on "Kết nối page"; retrying a 20-second job is pointless).
"""

from __future__ import annotations

import uuid
from typing import Any

from celery import shared_task
from sqlalchemy import text

from meobot.application.cskh.common import CskhClients
from meobot.application.cskh.sync import CskhSync
from meobot.core.logging import get_logger
from meobot.core.secrets import build_secret_box
from meobot.domain.cskh.models import MemberRole
from meobot.integrations.pancake.client import HttpPancakeClient, HttpPosClient
from meobot.tasks.runtime import TaskContext, run_async

logger = get_logger(__name__)

#: ``pg_try_advisory_lock`` keys (any constant unique in the app).
POLL_LOCK = 7_310_102
REFRESH_LOCK = 7_310_103


def _sync(context: TaskContext) -> CskhSync:
    clients = CskhClients(
        pancake=HttpPancakeClient(),
        pos=HttpPosClient(),
        secret_box=lambda: build_secret_box(context.settings),
    )
    return CskhSync(context.database.transaction, clients)


async def _locked(context: TaskContext, key: int, work: Any) -> dict[str, Any]:
    async with context.database.engine.connect() as connection:
        got = await connection.scalar(text("SELECT pg_try_advisory_lock(:k)"), {"k": key})
        if not got:
            return {"status": "ok", "message": "Lượt trước còn chạy, bỏ qua lượt này."}
        try:
            result: dict[str, Any] = await work()
            return result
        finally:
            await connection.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})


@shared_task(name="cskh.poll_pancake", soft_time_limit=170, time_limit=180)
def poll_pancake() -> dict[str, Any]:
    async def run(context: TaskContext) -> dict[str, Any]:
        sync = _sync(context)

        async def work() -> dict[str, Any]:
            report = await sync.poll()
            summary = report.summary()
            summary["status"] = "ok"
            return summary

        return await _locked(context, POLL_LOCK, work)

    return run_async(run)


@shared_task(name="cskh.refresh_meta", soft_time_limit=540, time_limit=600)
def refresh_meta() -> dict[str, Any]:
    async def run(context: TaskContext) -> dict[str, Any]:
        sync = _sync(context)

        async def work() -> dict[str, Any]:
            result = await sync.refresh()
            if result.get("errors"):
                result["status"] = "PARTIAL"
            return result

        return await _locked(context, REFRESH_LOCK, work)

    return run_async(run)


@shared_task(name="cskh.connect_pages", soft_time_limit=1700, time_limit=1800)
def connect_pages(
    page_ids: list[str],
    label_ids: list[str] | None = None,
    members: list[dict[str, str]] | None = None,
    import_history: bool = True,
) -> dict[str, Any]:
    async def run(context: TaskContext) -> dict[str, Any]:
        job = await _sync(context).connect(
            page_ids,
            label_ids=[uuid.UUID(x) for x in label_ids or []],
            members=[
                (uuid.UUID(m["user_id"]), MemberRole(m.get("role") or "STAFF"))
                for m in members or []
            ],
            import_history=import_history,
        )
        return {
            "status": "ok",
            "message": f"{job.get('connected', 0)}/{job.get('total', 0)} page đã nối, "
            f"{len(job.get('errors') or [])} lỗi",
        }

    return run_async(run)


__all__ = ["connect_pages", "poll_pancake", "refresh_meta"]
