"""Phòng Ads' scheduled pulls from Meta (0078, chốt 27/10).

* ``ads.sync_spend`` - 08:00 and 23:00 (Vietnam): yesterday's spend, messages,
  phone numbers. The 23:00 run re-reads the same day because Meta corrects it.
* ``ads.sync_charges`` - 08:00: billing charges over the last five days (and
  the whole previous month on the 1st-3rd).

* ``ads.sync_crm_revenue`` - every hour from 07:10 to 23:10: thực thu from the
  CRM (0083), this month so far (from the 1st of last month on the 1st-5th).

All take an explicit ``since`` / ``until`` (``YYYY-MM-DD``) for "Kéo lại
khoảng ngày", and return ``{"status": ..., "message": ...}`` so the job monitor
retries a half-failed run and reports it to the OWNER if it keeps failing.

Each BM is read and saved in its own transaction (03/11: on the 3rd the
charges run re-reads a whole month for ~300 accounts, Meta answered slowly and
the soft time limit dropped BOTH BMs' work), and the Meta runs get a longer
limit than the default 9 minutes - still under the monitor's 15-minute
``RUNNING_STALE_SECONDS``.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from celery import shared_task

from meobot.application.ads.crm_service import AdsCrmSyncService, CrmSyncReport, crm_window
from meobot.application.ads.sync_service import (
    AdsSyncService,
    SyncReport,
    charge_window,
    spend_window,
    vn_today,
)
from meobot.core.logging import get_logger
from meobot.integrations.crm.client import HttpCrmClient
from meobot.integrations.meta_ads.client import GraphMetaAdsClient
from meobot.tasks.runtime import TaskContext, run_async

logger = get_logger(__name__)

#: The Meta pulls' own limits (seconds): above Celery's default 540 / 600,
#: below the job monitor's ``RUNNING_STALE_SECONDS`` (900).
META_SOFT_TIME_LIMIT = 780
META_TIME_LIMIT = 840


def _days(since: str | None, until: str | None, default: tuple[date, date]) -> tuple[date, date]:
    if since and until:
        return date.fromisoformat(since), date.fromisoformat(until)
    return default


@shared_task(
    name="ads.sync_spend", soft_time_limit=META_SOFT_TIME_LIMIT, time_limit=META_TIME_LIMIT
)
def sync_spend(
    since: str | None = None, until: str | None = None, connection_id: str | None = None
) -> dict[str, Any]:
    start, end = _days(since, until, spend_window(vn_today()))
    return run_async(lambda context: _sync(context, "spend", start, end, connection_id))


@shared_task(
    name="ads.sync_charges", soft_time_limit=META_SOFT_TIME_LIMIT, time_limit=META_TIME_LIMIT
)
def sync_charges(
    since: str | None = None, until: str | None = None, connection_id: str | None = None
) -> dict[str, Any]:
    start, end = _days(since, until, charge_window(vn_today()))
    return run_async(lambda context: _sync(context, "charges", start, end, connection_id))


async def _sync(
    context: TaskContext, kind: str, since: date, until: date, connection_id: str | None
) -> dict[str, Any]:
    client = GraphMetaAdsClient()
    report = SyncReport(kind=kind, since=since, until=until)
    try:
        if connection_id:
            targets = [uuid.UUID(connection_id)]
        else:
            async with context.database.transaction() as session:
                targets = await AdsSyncService(
                    session, context.settings, client=client
                ).connection_ids()
        for target in targets:
            # One transaction per BM: a BM read in full is kept even if a
            # later one runs out of time.
            async with context.database.transaction() as session:
                service = AdsSyncService(session, context.settings, client=client)
                if kind == "spend":
                    part = await service.sync_spend(since, until, connection_id=target)
                else:
                    part = await service.sync_charges(since, until, connection_id=target)
            report.add(part)
    finally:
        await client.aclose()
    result = report.as_task_result()
    # Not ``extra=result``: its "message" key would clash with the LogRecord's
    # own and fail the run after the data was saved (27/10).
    logger.info("ads_synced", extra={"ads_sync": result})
    return result


@shared_task(name="ads.sync_crm_revenue")
def sync_crm_revenue(since: str | None = None, until: str | None = None) -> dict[str, Any]:
    start, end = _days(since, until, crm_window(vn_today()))
    return run_async(lambda context: _sync_crm(context, start, end))


async def _sync_crm(context: TaskContext, since: date, until: date) -> dict[str, Any]:
    key = context.settings.crm_api_key
    if key is None or not key.get_secret_value().strip():
        report = CrmSyncReport(since, until, configured=False)
    else:
        client = HttpCrmClient(context.settings.crm_api_base_url, key.get_secret_value())
        try:
            async with context.database.transaction() as session:
                report = await AdsCrmSyncService(session, client).sync(since, until)
        finally:
            await client.aclose()
    result = report.as_task_result()
    logger.info("ads_crm_synced", extra={"ads_sync": result})
    return result


__all__ = ["sync_charges", "sync_crm_revenue", "sync_spend"]
