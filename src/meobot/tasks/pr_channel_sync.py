"""Scheduled and on-demand channel syncing, on the existing worker stack.

Step 1F.2.4b. Three tasks, and between them the whole automatic half of the
connector:

* ``pr.sweep_channel_syncs`` - beat, hourly. Finds connections that are **due**,
  claims each one, and dispatches a job per claim.
* ``pr.sync_channel_metrics`` - one attempt at one channel.
* ``pr.release_stale_channel_syncs`` - beat, occasional. Frees connections
  stranded ``SYNCING`` by a worker that died, and drops the credential from Meta
  connect flows nobody finished choosing an account for.

This is the same shape ``pr_reviews`` already uses, deliberately: claim in one
committed transaction, dispatch afterwards, and have a recovery sweep for the
dispatch that gets lost. Nothing new was introduced to run background work -
MeoBot has Celery and a beat container, and a second scheduler would be a second
thing to operate and a second place for work to go missing.

Hourly sweep, daily sync
------------------------

The sweep runs every hour; a connection becomes due once a day. Those are
different numbers on purpose. The sweep is cheap - one indexed query that
usually returns nothing - and running it often means a channel connected at
14:00 does not wait until tomorrow's single sweep to be picked up. The *cadence*
is a day because channel-level figures do not move fast enough to justify
spending YouTube quota hourly, and because Analytics data for the last two days
is still settling anyway.

Why claim, commit, then dispatch
---------------------------------

The claim is a conditional UPDATE, and it is only a lock once it has committed.
Dispatching from inside the claiming transaction would let a worker start on a
connection whose claim later rolled back - two syncs, double the quota, and a
race on the insert. So the sweep commits its claims and only then sends tasks;
a task lost after that leaves a row ``SYNCING``, which the third task collects.

Syncs run on ``q_integrations``
--------------------------------

They are third-party calls, and that queue exists so a slow provider cannot
starve heartbeats, reminders or sheet syncs. A channel stuck behind a Google
timeout blocks nothing else.
"""

from __future__ import annotations

import uuid
from typing import Any

from celery import shared_task

from meobot.application.pr_services import build_pr_services
from meobot.core.logging import get_logger
from meobot.domain.pr.channel_connections import PrChannelSyncTrigger
from meobot.tasks.celery_app import QUEUE_INTEGRATIONS
from meobot.tasks.runtime import TaskContext, current_request_id, run_async

logger = get_logger(__name__)


@shared_task(name="pr.sweep_channel_syncs", queue=QUEUE_INTEGRATIONS)
def sweep_channel_syncs() -> dict[str, Any]:
    """Claim every connection that is due and hand each to a worker.

    Claims commit before anything is dispatched - see the module docstring. A
    connection that fails to claim was taken by somebody else in the meantime,
    which is the mechanism working rather than an error.
    """

    async def work(context: TaskContext) -> dict[str, Any]:
        if not context.settings.pr_channel_sync_enabled:
            return {"ok": True, "claimed": 0, "disabled": True}
        claimed: list[str] = []
        async with context.database.transaction() as session:
            services = build_pr_services(session, context.settings)
            for connection in await services.channel_sync.due_connections():
                if await services.channel_sync.claim(connection.id):
                    claimed.append(str(connection.id))
        # Outside the transaction: a task must never be dispatched for a claim
        # that has not committed.
        for connection_id in claimed:
            sync_channel_metrics.apply_async(kwargs={"connection_id": connection_id})
        if claimed:
            logger.info("pr_channel_syncs_dispatched", extra={"count": len(claimed)})
        return {"ok": True, "claimed": len(claimed)}

    return run_async(work)


@shared_task(name="pr.sync_channel_metrics", queue=QUEUE_INTEGRATIONS)
def sync_channel_metrics(connection_id: str, trigger: str = "SCHEDULED") -> dict[str, Any]:
    """Fetch one channel's numbers and record what happened.

    **No Celery-level retry**, and that is the same decision ``pr.run_ai_review``
    made. Retrying here would re-run a claimed job with no memory of why the
    last attempt failed, spending quota against a provider that has already said
    no. Retries live in the cadence instead: the connection's failure count
    drives a backoff, and the next sweep picks it up when that has elapsed.

    A failure is an outcome rather than an exception - the task records it and
    returns, because the caller is a scheduler and the next channel still needs
    syncing.
    """

    async def work(context: TaskContext) -> dict[str, Any]:
        async with context.database.transaction() as session:
            services = build_pr_services(session, context.settings)
            outcome = await services.channel_sync.run_sync(
                connection_id=uuid.UUID(connection_id),
                trigger=PrChannelSyncTrigger(trigger),
                request_id=current_request_id(),
                actor=context.system_actor(),
            )
        return {
            "ok": outcome.ok,
            "duplicate": outcome.duplicate,
            "snapshot_id": str(outcome.snapshot_id) if outcome.snapshot_id else None,
            "error_code": outcome.error_code.value if outcome.error_code else None,
        }

    return run_async(work)


@shared_task(name="pr.release_stale_channel_syncs", queue=QUEUE_INTEGRATIONS)
def release_stale_channel_syncs() -> dict[str, Any]:
    """Free connections stranded ``SYNCING`` by a worker that died.

    Without it a crashed worker holds a channel's claim for ever and that
    channel silently stops syncing - the quietest possible failure, and one
    nobody would notice until somebody asked why a number was three weeks old.
    """

    async def work(context: TaskContext) -> dict[str, Any]:
        async with context.database.transaction() as session:
            services = build_pr_services(session, context.settings)
            released = await services.channel_sync.release_stale()
            # Step 1F.2.4c. The same sweep drops credentials from connect flows
            # nobody finished. A parked Meta connection holds a long-lived
            # *user* token purely so an account chooser can list Pages, and
            # leaving one behind because somebody closed the tab is exactly the
            # stale secret not to leave lying around. Here rather than in a new
            # job: it is the same "collect what a person or a worker abandoned"
            # sweep, on the same schedule.
            expired = await services.channel_connections.expire_pending_selections()
        if released:
            logger.warning("pr_channel_syncs_released", extra={"count": released})
        return {"ok": True, "released": released, "selections_expired": expired}

    return run_async(work)


__all__: list[str] = [
    "release_stale_channel_syncs",
    "sweep_channel_syncs",
    "sync_channel_metrics",
]
