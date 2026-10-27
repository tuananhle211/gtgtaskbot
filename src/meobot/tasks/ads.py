"""Phòng Ads' scheduled pulls from Meta (0078, chốt 27/10).

* ``ads.sync_spend`` - 08:00 and 23:00 (Vietnam): yesterday's spend, messages,
  phone numbers. The 23:00 run re-reads the same day because Meta corrects it.
* ``ads.sync_charges`` - 08:00: billing charges over the last five days (and
  the whole previous month on the 1st-3rd).

Both take an explicit ``since`` / ``until`` (``YYYY-MM-DD``) for "Kéo lại
khoảng ngày", and return ``{"status": ..., "message": ...}`` so the job monitor
retries a half-failed run and reports it to the OWNER if it keeps failing.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from celery import shared_task

from meobot.application.ads.sync_service import (
    AdsSyncService,
    charge_window,
    spend_window,
    vn_today,
)
from meobot.core.logging import get_logger
from meobot.integrations.meta_ads.client import GraphMetaAdsClient
from meobot.tasks.runtime import TaskContext, run_async

logger = get_logger(__name__)


def _days(since: str | None, until: str | None, default: tuple[date, date]) -> tuple[date, date]:
    if since and until:
        return date.fromisoformat(since), date.fromisoformat(until)
    return default


@shared_task(name="ads.sync_spend")
def sync_spend(
    since: str | None = None, until: str | None = None, connection_id: str | None = None
) -> dict[str, Any]:
    start, end = _days(since, until, spend_window(vn_today()))
    return run_async(lambda context: _sync(context, "spend", start, end, connection_id))


@shared_task(name="ads.sync_charges")
def sync_charges(
    since: str | None = None, until: str | None = None, connection_id: str | None = None
) -> dict[str, Any]:
    start, end = _days(since, until, charge_window(vn_today()))
    return run_async(lambda context: _sync(context, "charges", start, end, connection_id))


async def _sync(
    context: TaskContext, kind: str, since: date, until: date, connection_id: str | None
) -> dict[str, Any]:
    client = GraphMetaAdsClient()
    target = uuid.UUID(connection_id) if connection_id else None
    try:
        async with context.database.transaction() as session:
            service = AdsSyncService(session, context.settings, client=client)
            if kind == "spend":
                report = await service.sync_spend(since, until, connection_id=target)
            else:
                report = await service.sync_charges(since, until, connection_id=target)
    finally:
        await client.aclose()
    result = report.as_task_result()
    # Not ``extra=result``: its "message" key would clash with the LogRecord's
    # own and fail the run after the data was saved (27/10).
    logger.info("ads_synced", extra={"ads_sync": result})
    return result


__all__ = ["sync_charges", "sync_spend"]
