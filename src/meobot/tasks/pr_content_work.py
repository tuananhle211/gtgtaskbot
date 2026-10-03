"""Content → Work projection, on the existing worker stack. M3.

Three tasks, and between them the whole asynchronous half of the projector:

* ``pr.sweep_content_work`` - beat, frequent. Claims committed ``PENDING`` rows
  and dispatches one projection task per content item;
* ``pr.project_content_work`` - one convergence run for one content item;
* ``pr.recover_stale_content_work`` - beat, occasional. Returns rows stranded
  ``RUNNING`` by a worker that died.

The same three-task shape ``pr_reviews`` already uses, for the same reasons, and
deliberately so: a second async pattern in one module would be a second set of
failure modes to learn.

Why the sweeper dispatches rather than the content transaction
---------------------------------------------------------------

The stage change and the ``PENDING`` row are written in one transaction, and the
worker must not see either before both commit. Dispatching from inside that
transaction would risk exactly that - a worker projecting content whose approval
rolled back.

So the queue row is the handoff and the sweeper is the dispatcher. It only ever
reads committed rows, it is the recovery path for a dispatch that was lost, and
it needs no after-commit hook in the application layer.

The cost is latency: work appears on a board within a sweep interval rather than
instantly. That is the right trade here - a KPI figure that is thirty seconds
late is correct, and a dispatch that can be lost is not.

Projection runs on ``q_default``
---------------------------------

Not ``q_integrations``: nothing here calls a third party. It is database work
against the same rows the web request just wrote, and putting it behind a slow
provider's queue would make a KPI board wait on a policy fetch.

Failure is never a content problem
-----------------------------------

A projection that raises leaves the content exactly as it was - the content
transaction committed long before - marks the queue row ``FAILED`` with a code,
and returns. The next request or an explicit reconcile picks it up, because the
projector is convergent: nothing is half-done, so nothing needs unwinding.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from celery import shared_task

from meobot.application.pr_services import build_pr_services
from meobot.core.logging import get_logger
from meobot.core.time import utcnow
from meobot.db.models.pr_content_work import PrContentWorkProjection
from meobot.domain.pr.content_work import PrContentWorkProjectionStatus
from meobot.tasks.celery_app import QUEUE_DEFAULT
from meobot.tasks.runtime import TaskContext, current_request_id, run_async

logger = get_logger(__name__)

#: How long a claim may be held before it is assumed lost. Generous: a
#: projection is seconds of work, and returning one that is merely slow would
#: put two workers on the same content - which the source-key index survives but
#: which wastes both.
STALE_AFTER = timedelta(minutes=10)


@shared_task(name="pr.sweep_content_work", queue=QUEUE_DEFAULT)
def sweep_content_work() -> dict[str, Any]:
    """Claim committed pending projections and hand each to a worker.

    Claiming and dispatching are separated on purpose: the claim commits (the
    row is ``RUNNING`` and nobody else will take it), and only then is a task
    sent. A dispatch lost after that leaves the row stranded ``RUNNING``, which
    :func:`recover_stale_content_work` collects - a bounded, visible failure
    rather than a projection that silently never happens.
    """

    async def work(context: TaskContext) -> dict[str, Any]:
        async with context.database.transaction() as session:
            services = build_pr_services(session, context.settings)
            claimed = [str(one) for one in await services.content_work.claim_batch()]
        # Outside the transaction: a task must never be dispatched for a claim
        # that has not committed.
        for content_id in claimed:
            project_content_work.apply_async(kwargs={"content_id": content_id})
        if claimed:
            logger.info("pr_content_work_dispatched", extra={"count": len(claimed)})
        return {"ok": True, "claimed": len(claimed)}

    return run_async(work)


@shared_task(name="pr.project_content_work", queue=QUEUE_DEFAULT)
def project_content_work(content_id: str) -> dict[str, Any]:
    """Converge one content item's work with its source.

    **No Celery-level retry.** The queue row is the durable record of what still
    needs doing, and it is visible to an operator; a second, invisible retry
    policy in the broker would multiply against the next sweep and make "how
    many times have we projected this" unanswerable.

    Never raises for a failure it can describe, so a piece with no mapping or an
    unreconstructable contributor does not poison the queue with retries that
    will fail identically. Those are **outcomes**, not errors - see
    :class:`~meobot.domain.pr.content_work.PrContentWorkOutcome`.
    """

    async def work(context: TaskContext) -> dict[str, Any]:
        request_id = current_request_id()
        target = uuid.UUID(content_id)
        try:
            async with context.database.transaction() as session:
                services = build_pr_services(session, context.settings)
                report = await services.content_work.settle(
                    # The worker's own actor, exactly as every other scheduled
                    # task acts. It is **not** the validator: the human who
                    # approved the content at source is what
                    # ``count_source_work`` records, and this actor could not
                    # approve anything if it tried.
                    actor=context.system_actor(),
                    request_id=request_id,
                    content_id=target,
                )
            return {
                "ok": True,
                "content_id": content_id,
                "content_code": report.content_code,
                "outcome": report.worst.value,
                "results": [
                    {"kind": one.kind.value, "outcome": one.outcome.value} for one in report.results
                ],
            }
        except Exception as error:  # deliberately broad; see the docstring
            logger.exception(
                "pr_content_work_projection_failed", extra={"pr_content_id": content_id}
            )
            await _mark_failed(context, target, type(error).__name__)
            return {"ok": False, "content_id": content_id, "error": type(error).__name__}

    return run_async(work)


@shared_task(name="pr.recover_stale_content_work", queue=QUEUE_DEFAULT)
def recover_stale_content_work() -> dict[str, Any]:
    """Return projections stranded ``RUNNING`` by a worker that died.

    Back to ``PENDING`` rather than ``FAILED``: nothing about the content
    changed, and the projector is convergent, so the honest state is *"still
    needs looking at"*.
    """

    async def work(context: TaskContext) -> dict[str, Any]:
        async with context.database.transaction() as session:
            services = build_pr_services(session, context.settings)
            recovered = await services.content_work.recover_stale(older_than=utcnow() - STALE_AFTER)
        if recovered:
            logger.warning("pr_content_work_recovered", extra={"count": recovered})
        return {"ok": True, "recovered": recovered}

    return run_async(work)


async def _mark_failed(context: TaskContext, content_id: uuid.UUID, code: str) -> None:
    """Record an unexpected failure on the queue row, in its own transaction.

    Its own transaction because the one that raised is gone - and the row has to
    say what happened, or the failure is a log line nobody reads and the content
    sits ``RUNNING`` until the stale sweeper quietly re-queues it for ever.

    ``code`` is an exception **class name**, never a message: an error string is
    an implementation detail that changes when somebody rewords a docstring, and
    storing one as operational state puts a stack trace on an admin screen.
    """
    from sqlalchemy import select

    try:
        async with context.database.transaction() as session:
            row = (
                (
                    await session.execute(
                        select(PrContentWorkProjection).where(
                            PrContentWorkProjection.content_id == content_id
                        )
                    )
                )
                .scalars()
                .one_or_none()
            )
            if row is not None:
                row.status = PrContentWorkProjectionStatus.FAILED
                row.settled_at = utcnow()
                row.last_error_code = code[:64]
    except Exception:  # pragma: no cover - the failure path's own failure
        logger.exception(
            "pr_content_work_mark_failed_failed", extra={"pr_content_id": str(content_id)}
        )


__all__: list[str] = [
    "STALE_AFTER",
    "project_content_work",
    "recover_stale_content_work",
    "sweep_content_work",
]
