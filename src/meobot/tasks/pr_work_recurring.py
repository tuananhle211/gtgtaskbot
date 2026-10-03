"""Recurring work generation, on the existing worker stack. M4B.

Two tasks:

* ``pr.sweep_recurring_work`` - beat. Finds active templates and dispatches one
  sweep per template;
* ``pr.generate_recurring_work`` - one template's backlog and catch-up.

The same claim-free, dispatch-then-work shape the rest of the module uses, with
one deliberate difference from ``pr.sweep_channel_syncs``: there is **no claim
column**. A channel sync claims because it spends third-party quota and two
concurrent syncs would cost twice. Recurring generation spends nothing and is
protected by two unique indexes instead - ``uq_template_occurrence`` on the
occurrence and ``uq_pr_work_items_source`` on the work - so two workers sweeping
one template concurrently produce one occurrence and one set of work items
without a claim to get stuck in.

That matters because a claim is a lock somebody has to remember to release. The
recovery task this module does **not** need is the recovery task
``pr_channel_sync`` does.

Why the session is not wrapped in a transaction
------------------------------------------------

Every other PR task uses ``context.database.transaction()`` and lets the caller
own the boundary. This one uses ``context.database.session()``, because the
generator's correctness *is* a sequence of commits: the occurrence is committed
as ``PENDING`` on its own so that a crash leaves evidence the work was owed, and
only then are the work items and the settlement committed together. A single
enclosing transaction would collapse those into one, and the crash-recovery
guarantee with them.

No Celery-level retry
----------------------

The same decision ``pr.sync_channel_metrics`` made. A retry here would re-run a
sweep with no memory of why the last one failed; the generator already records
each failed occurrence as ``FAILED_RETRYABLE`` with its message and picks it up
from the retry set on the next sweep. Retries live in the cadence.

Sweeps run on ``q_default``
----------------------------

Nothing here calls a third party. It is database work against the department's
own rows, and putting it behind the integrations queue would make tomorrow's
routine work wait on a slow provider.
"""

from __future__ import annotations

import uuid
from typing import Any

from celery import shared_task

from meobot.application.pr_services import build_pr_services
from meobot.core.logging import get_logger
from meobot.tasks.celery_app import QUEUE_DEFAULT
from meobot.tasks.runtime import TaskContext, current_request_id, run_async

logger = get_logger(__name__)


@shared_task(name="pr.sweep_recurring_work", queue=QUEUE_DEFAULT)
def sweep_recurring_work() -> dict[str, Any]:
    """Find every active template and hand each to a worker.

    Reads only. The dispatch is deliberately outside any write, because there is
    nothing to claim: a template dispatched twice produces the same occurrences
    and the same work, and the database says so.
    """

    async def work(context: TaskContext) -> dict[str, Any]:
        if not context.settings.pr_recurring_work_enabled:
            return {"ok": True, "dispatched": 0, "disabled": True}
        async with context.database.session() as session:
            services = build_pr_services(session, context.settings)
            due = await services.work_recurring_generator.due_templates()
            templates = [str(one) for one in due]
        for template_id in templates:
            generate_recurring_work.apply_async(kwargs={"template_id": template_id})
        if templates:
            logger.info("pr_recurring_sweep_dispatched", extra={"count": len(templates)})
        return {"ok": True, "dispatched": len(templates)}

    return run_async(work)


@shared_task(name="pr.generate_recurring_work", queue=QUEUE_DEFAULT)
def generate_recurring_work(template_id: str) -> dict[str, Any]:
    """Settle one template's backlog and walk it forward to now.

    A failure is an **outcome** rather than an exception: the sweep has other
    templates to get through, the generator has already recorded which
    occurrence failed and why, and raising here would only convert a visible row
    into a Celery traceback nobody is reading at four in the morning.
    """

    async def work(context: TaskContext) -> dict[str, Any]:
        async with context.database.session() as session:
            services = build_pr_services(session, context.settings)
            outcome = await services.work_recurring_generator.sweep_template(
                template_id=uuid.UUID(template_id), request_id=current_request_id()
            )
        if outcome.generated or outcome.failed or outcome.skipped_closed_period:
            logger.info(
                "pr_recurring_template_swept",
                extra={
                    "pr_recurring_template_id": template_id,
                    "generated": outcome.generated,
                    "skipped_closed_period": outcome.skipped_closed_period,
                    "failed": outcome.failed,
                },
            )
        return {
            "ok": True,
            "template_id": template_id,
            "evaluated": outcome.evaluated,
            "generated": outcome.generated,
            "skipped_closed_period": outcome.skipped_closed_period,
            "failed": outcome.failed,
        }

    return run_async(work)


__all__: list[str] = ["generate_recurring_work", "sweep_recurring_work"]
