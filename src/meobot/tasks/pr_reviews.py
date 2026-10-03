"""PR AI review execution, on the existing worker stack.

Step 1F. Three tasks, and between them the whole asynchronous half of the gate:

* ``pr.sweep_ai_review_runs`` - beat, frequent. Claims committed ``QUEUED`` rows
  and dispatches one execution task per run.
* ``pr.run_ai_review`` - one attempt at one run.
* ``pr.recover_stale_ai_review_runs`` - beat, occasional. Returns runs stranded
  ``RUNNING`` by a worker that died.

Why the sweeper dispatches rather than the workflow
----------------------------------------------------

The stage change and the ``QUEUED`` row are written in one transaction, and the
worker must not see either before both commit. Dispatching from inside that
transaction would risk exactly that - a worker picking up a run for content that
never entered ``AI_REVIEW``, or worse, one whose transaction rolled back.

So the queue row is the handoff and the sweeper is the dispatcher. It only ever
reads committed rows, it is the recovery path for a dispatch that was lost, and
it needs no after-commit hook in the application layer. It is the same shape as
``notifications.drain_outbox``, which this repository already runs every twenty
seconds for the same reason.

The cost is latency: a review starts within one sweep interval rather than
instantly. The web panel polls while a run is active, so what a person sees is
"Đang chờ xử lý…" for a few seconds - which is true, and better than a dispatch
that can be lost.

Reviews run on ``q_integrations``
---------------------------------

They are third-party calls, and the queue exists so a slow or down provider
cannot starve heartbeats, reminders or sheet syncs. The claim/settle protocol
means a review stuck behind a provider timeout blocks nothing else: the content
stays in ``AI_REVIEW`` and every other stage keeps moving.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from celery import Task, shared_task

from meobot.application.pr_ai_review_executor import PrAiReviewExecutor
from meobot.application.pr_policy_source_service import PrPolicySourceService
from meobot.application.pr_services import build_pr_services
from meobot.core.logging import get_logger
from meobot.integrations.platform_policy.fetcher import PolicySourceFetcher
from meobot.tasks.celery_app import QUEUE_INTEGRATIONS
from meobot.tasks.runtime import TaskContext, current_request_id, run_async

logger = get_logger(__name__)


@shared_task(name="pr.sweep_ai_review_runs", queue=QUEUE_INTEGRATIONS)
def sweep_ai_review_runs() -> dict[str, Any]:
    """Claim committed queued reviews and hand each to a worker.

    Claiming and dispatching are separated on purpose: the claim commits (the
    run is ``RUNNING`` and nobody else will take it), and only then is a task
    sent. A dispatch lost after that leaves the run stranded ``RUNNING``, which
    is what :func:`recover_stale_ai_review_runs` exists to collect - a bounded,
    visible failure rather than a review that silently never happens.
    """

    async def work(context: TaskContext) -> dict[str, Any]:
        if not context.settings.pr_ai_review_enabled:
            return {"ok": True, "claimed": 0, "disabled": True}
        async with context.database.transaction() as session:
            services = build_pr_services(session, context.settings)
            claimed = [str(run.id) for run in await services.ai_review_runs.claim_batch()]
        # Outside the transaction: a task must never be dispatched for a claim
        # that has not committed.
        for run_id in claimed:
            run_ai_review.apply_async(kwargs={"run_id": run_id})
        if claimed:
            logger.info("ai_review_runs_dispatched", extra={"count": len(claimed)})
        return {"ok": True, "claimed": len(claimed)}

    return run_async(work)


@shared_task(bind=True, name="pr.run_ai_review", queue=QUEUE_INTEGRATIONS)
def run_ai_review(self: Task[Any, Any], run_id: str) -> dict[str, Any]:
    """Execute one claimed review.

    **No Celery-level retry.** Retries live in the run row, where they are
    durable, visible in the web panel and bounded by
    ``pr_ai_review_max_attempts``; a second, invisible retry policy in the
    broker would multiply against the first and make "how many times did we ask
    the provider" unanswerable. A transient failure re-queues the run, and the
    next sweep picks it up.

    Never raises for an expected failure, so a bad draft or a down provider does
    not poison the queue with retries that will fail identically.
    """

    async def work(context: TaskContext) -> dict[str, Any]:
        request_id = current_request_id()
        target = uuid.UUID(run_id)

        async with context.database.transaction() as session:
            services = build_pr_services(session, context.settings)
            run = await services.ai_review_runs.get(target)
            if run is None:
                logger.warning("ai_review_run_missing", extra={"pr_ai_review_run_id": run_id})
                return {"ok": False, "run_id": run_id, "error": "run_missing"}

            executor = PrAiReviewExecutor(
                session,
                services.ai_review_runs,
                services.ai_reviews,
                context.llm,
            )
            outcome = await executor.execute(
                # The worker's own actor, exactly as every other scheduled task
                # acts. It is *not* the AI's identity: nothing attributes a
                # review to a person, ``pr_ai_reviews`` has no user column, and
                # this actor cannot approve anything.
                actor=context.system_actor(),
                request_id=request_id,
                run=run,
            )

        return {
            "ok": outcome.error_code is None,
            "run_id": run_id,
            "status": outcome.status.value,
            "outcome": outcome.outcome.value if outcome.outcome else None,
            "new_stage": outcome.new_stage.value if outcome.new_stage else None,
            "error": outcome.error_code,
            "will_retry": outcome.will_retry,
        }

    return run_async(work)


@shared_task(name="pr.recover_stale_ai_review_runs", queue=QUEUE_INTEGRATIONS)
def recover_stale_ai_review_runs() -> dict[str, Any]:
    """Return runs stranded ``RUNNING`` by a worker that died mid-review.

    A claim older than ``pr_ai_review_stale_after_seconds`` is assumed lost -
    the same decision the notification outbox makes, and for the same reason: a
    heartbeat protocol would be more machinery than the failure is worth. A run
    with attempts left goes back to ``QUEUED``; one without becomes ``FAILED``,
    where a person can retry it and see why.
    """

    async def work(context: TaskContext) -> dict[str, Any]:
        async with context.database.transaction() as session:
            services = build_pr_services(session, context.settings)
            recovered = await services.ai_review_runs.recover_stale(
                stale_after=timedelta(seconds=context.settings.pr_ai_review_stale_after_seconds)
            )
        return {"ok": True, "recovered": recovered}

    return run_async(work)


@shared_task(name="pr.refresh_policy_sources", queue=QUEUE_INTEGRATIONS)
def refresh_policy_sources() -> dict[str, Any]:
    """Re-read the official policy pages. **Changes no ACTIVE pack.**

    Step 1F.1, and low frequency on purpose: policy pages change rarely, and
    hammering them would be both rude and pointless.

    A source whose text is unchanged produces nothing. A source that changed
    produces a new snapshot and a log line - and *still* changes nothing about
    production, because turning a snapshot into the rules reviews use takes an
    explicit ``policy packs build`` and ``policy packs activate``. An official
    page changing is not a decision, and nobody reviewed it.

    A failure is contained: the source is logged and skipped, the rest continue,
    and yesterday's ACTIVE pack keeps grounding reviews. Today's fetch failing
    must never stop content being reviewed.
    """

    async def work(context: TaskContext) -> dict[str, Any]:
        if not context.settings.pr_policy_refresh_enabled:
            return {"ok": True, "refreshed": 0, "disabled": True}
        async with context.database.transaction() as session:
            services = build_pr_services(session, context.settings)
            sources = PrPolicySourceService(session, PolicySourceFetcher())
            outcomes = await sources.refresh_all()
            _ = services  # bundle built for parity with the other tasks
        changed = [outcome.source_family for outcome in outcomes if outcome.changed]
        failed = [outcome.source_family for outcome in outcomes if outcome.error_code]
        logger.info(
            "policy_source_refresh_completed",
            extra={"sources": len(outcomes), "changed": len(changed), "failed": len(failed)},
        )
        return {
            "ok": True,
            "sources": len(outcomes),
            "changed": changed,
            "failed": failed,
        }

    return run_async(work)


__all__: list[str] = [
    "recover_stale_ai_review_runs",
    "refresh_policy_sources",
    "run_ai_review",
    "sweep_ai_review_runs",
]
