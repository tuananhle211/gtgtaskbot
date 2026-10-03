"""The reminder sweep.

Runs every minute and does the least possible: find reminders whose moment has
arrived, write one occurrence and one outbound message per firing, advance the
schedule. Telegram is never touched here - the q_notifications worker sends
what this produces, which is the same path every other cross-chat message takes.

Why a sweep rather than one scheduled task per reminder: a task queued for next
Thursday survives neither a broker restart nor a redeploy, and a reminder that
quietly stopped existing is exactly the failure this module was written to end.
A row in a table with an index on ``next_run_at`` survives both.

Two Beat processes running this at once is safe. Everything it inserts is
protected by ``uq_reminder_occurrences_reminder_moment``, so the second one
loses the race and writes nothing.
"""

from __future__ import annotations

from typing import Any

from celery import shared_task

from meobot.application.notification_router import NotificationRouter
from meobot.application.reminder_service import ReminderService
from meobot.core.logging import get_logger
from meobot.tasks.runtime import TaskContext, run_async

logger = get_logger(__name__)


@shared_task(name="reminders.sweep_due")
def sweep_due(limit: int | None = None) -> dict[str, Any]:
    """Fire every reminder whose moment has arrived."""
    return run_async(lambda context: _sweep(context, limit))


async def _sweep(context: TaskContext, limit: int | None) -> dict[str, Any]:
    settings = context.settings
    if not settings.reminder_enabled:
        return {"fired": 0, "skipped": "disabled"}

    fired = duplicate = 0
    async with context.database.transaction() as session:
        service = ReminderService(session, settings)
        router = NotificationRouter(session, settings)
        due = await service.due_batch(limit=limit)
        for reminder in due:
            occurrence = await service.fire(reminder, router=router)
            if occurrence is None:
                duplicate += 1
            else:
                fired += 1

    if fired or duplicate:
        logger.info("reminders_swept", extra={"fired": fired, "already_handled": duplicate})
    return {"fired": fired, "already_handled": duplicate}


#: Occurrences are settled by the delivery worker at the moment Telegram
#: accepts or finally refuses the message - see ``_on_delivered`` in
#: :mod:`meobot.tasks.notifications`. There is deliberately no polling task
#: here: a second place that decides whether a reminder was delivered is a
#: second place that can disagree with the first.
