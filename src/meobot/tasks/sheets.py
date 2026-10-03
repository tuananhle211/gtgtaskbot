"""Google Sheets synchronisation tasks.

These run on ``q_integrations`` so a Google outage cannot starve the rest of
the system. Both tasks are safe to run repeatedly: the sync service is
idempotent, and a re-run of an unchanged sheet creates nothing.
"""

from __future__ import annotations

import uuid
from typing import Any

from celery import Task, shared_task

from meobot.application.audit_service import AuditService
from meobot.application.script_sync_service import (
    ScriptSyncService,
    SyncReport,
    summarize_reports,
)
from meobot.application.sheet_profile_service import SheetProfileService
from meobot.core.logging import get_logger
from meobot.tasks.celery_app import QUEUE_INTEGRATIONS, RETRY_KWARGS
from meobot.tasks.runtime import TaskContext, current_request_id, run_async

logger = get_logger(__name__)


@shared_task(
    bind=True,
    name="sheets.sync_profile",
    queue=QUEUE_INTEGRATIONS,
    autoretry_for=(ConnectionError, TimeoutError),
    retry_kwargs=RETRY_KWARGS,
    retry_backoff=True,
    retry_backoff_max=120,
    retry_jitter=True,
)
def sync_profile(
    self: Task[Any, Any],
    profile_id: str,
    *,
    notify_chat_id: int | None = None,
) -> dict[str, Any]:
    """Synchronise one sheet profile.

    Args:
        profile_id: UUID of the profile to read.
        notify_chat_id: Telegram chat that asked for the sync, if any.

    Returns:
        The sync report as a JSON-safe dict.
    """

    async def work(context: TaskContext) -> dict[str, Any]:
        request_id = current_request_id()
        async with context.database.transaction() as session:
            audit = AuditService(session)
            profiles = SheetProfileService(session, audit)
            profile = await profiles.get(uuid.UUID(profile_id))
            report = await ScriptSyncService(session, audit, context.sheets, profiles).sync_profile(
                actor=context.system_actor(),
                request_id=request_id,
                profile=profile,
            )
        await _notify(context, [report], notify_chat_id)
        return dict(report.as_dict())

    return run_async(work)


@shared_task(
    bind=True,
    name="sheets.sync_all_active_profiles",
    queue=QUEUE_INTEGRATIONS,
    autoretry_for=(ConnectionError, TimeoutError),
    retry_kwargs=RETRY_KWARGS,
    retry_backoff=True,
    retry_jitter=True,
)
def sync_all_active_profiles(
    self: Task[Any, Any],
    *,
    notify_chat_id: int | None = None,
) -> dict[str, Any]:
    """Synchronise every active profile, one after another.

    Sequential on purpose: the Sheets API quota is per project, and a NAS with
    two worker processes gains nothing from hammering it in parallel.
    """

    async def work(context: TaskContext) -> dict[str, Any]:
        request_id = current_request_id()
        actor = context.system_actor()
        reports: list[SyncReport] = []
        async with context.database.transaction() as session:
            audit = AuditService(session)
            profiles = SheetProfileService(session, audit)
            syncer = ScriptSyncService(session, audit, context.sheets, profiles)
            for profile in await profiles.list_syncable():
                reports.append(
                    await syncer.sync_profile(actor=actor, request_id=request_id, profile=profile)
                )

        await _notify(context, reports, notify_chat_id, only_when_interesting=True)
        return {
            "profiles": len(reports),
            "created": sum(report.created for report in reports),
            "new_versions": sum(report.new_versions for report in reports),
            "failed": sum(1 for report in reports if report.failed),
            "needs_remap": sum(1 for report in reports if report.needs_remap),
        }

    return run_async(work)


async def _notify(
    context: TaskContext,
    reports: list[SyncReport],
    notify_chat_id: int | None,
    *,
    only_when_interesting: bool = False,
) -> None:
    """Tell the requester (or the Owner) what the run did.

    A scheduled run stays quiet unless something needs a human: new content,
    an invalidated approval, a failure, or a schema change.
    """
    chat_id = notify_chat_id or context.owner_chat_id
    if chat_id is None or not reports:
        return

    interesting = any(
        report.failed or report.needs_remap or report.touched or report.invalidated_approvals
        for report in reports
    )
    if only_when_interesting and not interesting:
        return

    await context.notifier.send(chat_id, summarize_reports(reports))
