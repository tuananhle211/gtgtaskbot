"""The lifecycle of an AI review execution: queue, claim, settle, recover.

Step 1F. This service owns ``pr_ai_review_runs`` and nothing else. It does not
call a model, does not write ``pr_ai_reviews`` and does not move content - those
belong to :class:`~meobot.application.pr_ai_review_executor.PrAiReviewExecutor`
and :class:`~meobot.application.pr_ai_review_service.PrAiReviewService`. Keeping
it that narrow is what lets
:class:`~meobot.application.pr_workflow_service.PrContentWorkflowService` depend
on it without a cycle: the workflow enqueues, and knows nothing about providers.

```
workflow enters AI_REVIEW ─→ enqueue()  [same transaction, commits together]
                                 │
beat: pr.sweep_ai_review_runs ─→ claim_batch() ─→ dispatch pr.run_ai_review
                                 │
                            executor ─→ mark_succeeded / mark_failed / mark_superseded
                                 │
beat: recover_stale() ─────────→ RUNNING too long → QUEUED again, or FAILED
```

Why the queue row is written in the workflow's transaction
----------------------------------------------------------

A worker must never read a stage change that has not committed. Writing the
``QUEUED`` row inside the same transaction as the transition makes the two
atomic: if the transition rolls back, so does the run, and there is no orphan
job for content that never entered ``AI_REVIEW``. Dispatch happens afterwards,
from a sweeper that only ever sees committed rows - which is the same shape the
notification outbox already uses in this repository, and the reason no separate
after-commit hook was invented for this step.

Idempotency is the database's job
---------------------------------

``uq_pr_ai_review_runs_active`` is a partial unique index over
``(content_id, content_version_id, review_type)`` where the status is ``QUEUED``
or ``RUNNING``. :meth:`enqueue` checks first and then *also* catches the
integrity error, because the check and the insert are not atomic against a
concurrent transaction. Two callers racing produce one run and one ``None``,
not two runs and a duplicated review.

Claiming uses ``FOR UPDATE SKIP LOCKED``, so two sweepers running at once take
disjoint work rather than blocking or double-dispatching.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.pr_policy_readiness_service import PolicyPin
from meobot.core.logging import get_logger
from meobot.core.time import utcnow
from meobot.db.models.pr_ai_review_run import PrAiReviewRun
from meobot.db.models.pr_platform_policy import PrAiReviewRunPolicyPack
from meobot.domain.pr.ai_review import FULL_REVIEW_PROMPT_VERSION
from meobot.domain.pr.models import (
    ACTIVE_RUN_STATUSES,
    PrAiReviewResult,
    PrAiReviewRunStatus,
    PrAiReviewTrigger,
    PrAiReviewType,
)

logger = get_logger(__name__)

#: How many runs one sweep claims. Bounded so a backlog cannot spend an entire
#: LLM budget in one tick - the same reasoning as ``REVIEW_BATCH_SIZE`` in the
#: script review sweep.
CLAIM_BATCH_SIZE = 5


class PrAiReviewRunService:
    """Durable execution state for automated reviews.

    Args:
        session: Unit of work. The caller owns the transaction boundary, as
            everywhere else in this application layer.
        max_attempts: How many times one run may be started before it is
            failed for good. Bounds the retry policy; a run that has burned
            them all stays ``FAILED`` until a person asks again.
    """

    def __init__(self, session: AsyncSession, *, max_attempts: int = 3) -> None:
        self._session = session
        self._max_attempts = max(1, max_attempts)

    # --- Queueing ---------------------------------------------------------
    async def enqueue(
        self,
        *,
        content_id: uuid.UUID,
        content_version_id: uuid.UUID,
        trigger: PrAiReviewTrigger = PrAiReviewTrigger.AUTO,
        requested_by_user_id: uuid.UUID | None = None,
        review_type: PrAiReviewType = PrAiReviewType.FULL_REVIEW,
        policy_pins: Sequence[PolicyPin] = (),
    ) -> PrAiReviewRun | None:
        """Queue one execution, or return ``None`` if one is already active.

        Idempotent by design: entering ``AI_REVIEW`` twice, or a retry racing
        the automatic trigger, produces one run. ``None`` is a success - it
        means the work is already going to happen - and callers must not treat
        it as a failure.

        The row is written in the caller's transaction, so it commits with the
        stage change that caused it or not at all.
        """
        if await self.active_for_version(content_id, content_version_id, review_type) is not None:
            return None

        run = PrAiReviewRun(
            content_id=content_id,
            content_version_id=content_version_id,
            review_type=review_type,
            trigger=trigger,
            status=PrAiReviewRunStatus.QUEUED,
            requested_by_user_id=requested_by_user_id,
            prompt_version=FULL_REVIEW_PROMPT_VERSION,
            attempt_count=0,
        )
        self._session.add(run)
        try:
            # A savepoint, so losing the race does not poison the caller's
            # transaction - the workflow transition around this must still
            # commit.
            async with self._session.begin_nested():
                await self._session.flush()
        except IntegrityError:
            # The partial unique index fired: somebody else queued the same
            # draft between the check above and this insert. Their run is the
            # one that will happen, which is exactly the outcome wanted.
            logger.info(
                "ai_review_queue_raced",
                extra={
                    "pr_content_id": str(content_id),
                    "pr_content_version_id": str(content_version_id),
                },
            )
            return None

        # Step 1F.1: pinned **here**, at queue time, in this transaction. A
        # pack activated between now and the worker starting cannot change what
        # this run is judged against, because the worker reads these rows rather
        # than asking which pack is active.
        for pin in policy_pins:
            self._session.add(
                PrAiReviewRunPolicyPack(
                    run_id=run.id,
                    policy_pack_id=pin.pack.id,
                    platform_code=pin.platform_code,
                    distribution_mode=pin.distribution_mode,
                )
            )
        if policy_pins:
            await self._session.flush()

        logger.info(
            "ai_review_queued",
            extra={
                "pr_ai_review_run_id": str(run.id),
                "pr_content_id": str(content_id),
                "pr_content_version_id": str(content_version_id),
                "trigger": trigger.value,
                "prompt_version": run.prompt_version,
                "policy_packs": [pin.pack.label for pin in policy_pins],
            },
        )
        return run

    async def pinned_packs(self, run_id: uuid.UUID) -> Sequence[PrAiReviewRunPolicyPack]:
        """Which packs this run pinned. Empty for a pre-1F.1 run, which is legacy.

        A legacy run is reported as ungrounded rather than backfilled: attaching
        today's pack to a review that never saw it would be inventing history.
        """
        result = await self._session.execute(
            select(PrAiReviewRunPolicyPack)
            .where(PrAiReviewRunPolicyPack.run_id == run_id)
            .order_by(PrAiReviewRunPolicyPack.platform_code)
        )
        return result.scalars().all()

    # --- Reading ----------------------------------------------------------
    async def active_for_version(
        self,
        content_id: uuid.UUID,
        content_version_id: uuid.UUID,
        review_type: PrAiReviewType = PrAiReviewType.FULL_REVIEW,
    ) -> PrAiReviewRun | None:
        """The queued or running execution for one draft, if there is one."""
        result = await self._session.execute(
            select(PrAiReviewRun)
            .where(
                PrAiReviewRun.content_id == content_id,
                PrAiReviewRun.content_version_id == content_version_id,
                PrAiReviewRun.review_type == review_type,
                PrAiReviewRun.status.in_(sorted(ACTIVE_RUN_STATUSES)),
            )
            .limit(1)
        )
        return result.scalars().first()

    async def latest_for_content(
        self,
        content_id: uuid.UUID,
        *,
        content_version_id: uuid.UUID | None = None,
        review_type: PrAiReviewType = PrAiReviewType.FULL_REVIEW,
    ) -> PrAiReviewRun | None:
        """The newest execution for one item, optionally for one draft.

        What the detail screen renders and polls. Scoped to a version when the
        caller knows which draft is on screen, so a finished run about a draft
        that has since been rewritten does not present itself as the current
        state of the new one.
        """
        statement = select(PrAiReviewRun).where(
            PrAiReviewRun.content_id == content_id,
            PrAiReviewRun.review_type == review_type,
        )
        if content_version_id is not None:
            statement = statement.where(PrAiReviewRun.content_version_id == content_version_id)
        result = await self._session.execute(
            statement.order_by(PrAiReviewRun.created_at.desc()).limit(1)
        )
        return result.scalars().first()

    async def get(self, run_id: uuid.UUID) -> PrAiReviewRun | None:
        return await self._session.get(PrAiReviewRun, run_id)

    # --- Claiming ---------------------------------------------------------
    async def claim_batch(self, *, limit: int = CLAIM_BATCH_SIZE) -> Sequence[PrAiReviewRun]:
        """Take up to ``limit`` queued runs and mark them ``RUNNING``.

        ``FOR UPDATE SKIP LOCKED`` so two sweepers take disjoint work instead of
        blocking on each other or handing the same run to two workers. The
        attempt counter increments here rather than in the executor: a worker
        that dies before writing anything has still used an attempt, and
        without that a crash loop would retry for ever.
        """
        statement = (
            select(PrAiReviewRun)
            .where(PrAiReviewRun.status == PrAiReviewRunStatus.QUEUED)
            .order_by(PrAiReviewRun.created_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        rows = list((await self._session.execute(statement)).scalars().all())
        moment = utcnow()
        claimed: list[PrAiReviewRun] = []
        for row in rows:
            if row.attempt_count >= self._max_attempts:
                # Queued but out of attempts - recovery re-queued it once too
                # often. Settle it rather than dispatching work that will only
                # burn provider budget.
                self._settle(row, PrAiReviewRunStatus.FAILED, error_code="attempts_exhausted")
                continue
            row.status = PrAiReviewRunStatus.RUNNING
            row.started_at = moment
            row.attempt_count += 1
            claimed.append(row)
        await self._session.flush()
        return claimed

    async def recover_stale(self, *, stale_after: timedelta) -> int:
        """Return runs stranded ``RUNNING`` by a worker that died.

        Rather than a heartbeat protocol, a claim older than ``stale_after`` is
        assumed lost - the same decision the notification outbox already makes.
        A run with attempts left goes back to ``QUEUED``; one without becomes
        ``FAILED``, because re-queueing for ever is how a broken provider turns
        into an unbounded bill.
        """
        cutoff = utcnow() - stale_after
        result = await self._session.execute(
            select(PrAiReviewRun)
            .where(
                PrAiReviewRun.status == PrAiReviewRunStatus.RUNNING,
                PrAiReviewRun.started_at.is_not(None),
                PrAiReviewRun.started_at < cutoff,
            )
            .with_for_update(skip_locked=True)
        )
        recovered = 0
        for row in result.scalars().all():
            if row.attempt_count >= self._max_attempts:
                self._settle(row, PrAiReviewRunStatus.FAILED, error_code="timed_out")
            else:
                row.status = PrAiReviewRunStatus.QUEUED
                row.started_at = None
            recovered += 1
            logger.warning(
                "ai_review_run_recovered",
                extra={
                    "pr_ai_review_run_id": str(row.id),
                    "attempt": row.attempt_count,
                    "status": row.status.value,
                },
            )
        await self._session.flush()
        return recovered

    # --- Settling ---------------------------------------------------------
    async def mark_succeeded(
        self,
        run: PrAiReviewRun,
        *,
        review_id: uuid.UUID,
        outcome: PrAiReviewResult,
        model_name: str,
        model_version: str | None,
    ) -> None:
        """The run produced an appended review and the workflow moved on."""
        if await self._vanished(run):
            return
        run.review_id = review_id
        run.outcome = outcome
        run.model_name = model_name
        run.model_version = model_version
        self._settle(run, PrAiReviewRunStatus.SUCCEEDED)
        await self._session.flush()

    async def mark_superseded(
        self,
        run: PrAiReviewRun,
        *,
        outcome: PrAiReviewResult | None,
        model_name: str | None = None,
        model_version: str | None = None,
        reason: str,
    ) -> None:
        """The answer arrived about a draft or a stage that has moved on.

        The outcome is kept so the execution still says what it concluded, and
        **no review row is written**: ``PrAiReviewService.record_review`` records
        only reviews of the current draft at ``AI_REVIEW``, and Step 1F does not
        weaken that. Nothing transitions.
        """
        if await self._vanished(run):
            return
        run.outcome = outcome
        run.model_name = model_name
        run.model_version = model_version
        self._settle(run, PrAiReviewRunStatus.SUPERSEDED, error_code=reason)
        await self._session.flush()
        logger.info(
            "ai_review_superseded",
            extra={
                "pr_ai_review_run_id": str(run.id),
                "pr_content_id": str(run.content_id),
                "pr_content_version_id": str(run.content_version_id),
                "reason": reason,
            },
        )

    async def mark_failed(self, run: PrAiReviewRun, *, error_code: str) -> None:
        """Give up on this execution. The content stays where it is."""
        if await self._vanished(run):
            return
        self._settle(run, PrAiReviewRunStatus.FAILED, error_code=error_code)
        await self._session.flush()
        logger.warning(
            "ai_review_failed",
            extra={
                "pr_ai_review_run_id": str(run.id),
                "pr_content_id": str(run.content_id),
                "attempt": run.attempt_count,
                "error_code": error_code,
            },
        )

    async def requeue(self, run: PrAiReviewRun, *, error_code: str) -> bool:
        """Put a transient failure back in the queue, if attempts remain.

        Returns whether it will be tried again. ``False`` means the run has
        been failed for good, so the caller does not also need to decide.
        """
        if run.attempt_count >= self._max_attempts:
            await self.mark_failed(run, error_code=error_code)
            return False
        if await self._vanished(run):
            return False
        run.status = PrAiReviewRunStatus.QUEUED
        run.started_at = None
        run.error_code = error_code
        await self._session.flush()
        logger.info(
            "ai_review_requeued",
            extra={
                "pr_ai_review_run_id": str(run.id),
                "attempt": run.attempt_count,
                "error_code": error_code,
            },
        )
        return True

    @property
    def max_attempts(self) -> int:
        return self._max_attempts

    # --- Internals --------------------------------------------------------
    async def _vanished(self, run: PrAiReviewRun) -> bool:
        """Has this run's row been deleted underneath the worker?

        Step 1F.2.3a. Permanent content deletion removes the runs belonging to
        the item, and a worker that claimed one a second earlier is still holding
        the object. Writing to it would emit an ``UPDATE`` matching no rows,
        which SQLAlchemy raises as ``StaleDataError`` - an unhandled exception in
        a Celery task, and then a retry storm against content that no longer
        exists.

        So every settling path asks first, and a vanished run is a **clean exit**:
        nothing is written, nothing is retried, and the object is detached so a
        later flush in the same session cannot resurrect the update. The answer
        the worker was carrying is simply dropped, which is correct - it is an
        answer about a draft nobody can read any more.

        One extra ``SELECT`` per settle, on a primary key. The alternative -
        catching ``StaleDataError`` - would have to distinguish "deleted" from
        "somebody else settled it", and those need different responses.
        """
        found = await self._session.execute(
            select(PrAiReviewRun.id).where(PrAiReviewRun.id == run.id)
        )
        if found.scalars().first() is not None:
            return False
        logger.info(
            "ai_review_run_vanished",
            extra={"pr_ai_review_run_id": str(run.id), "pr_content_id": str(run.content_id)},
        )
        if run in self._session:
            self._session.expunge(run)
        return True

    @staticmethod
    def _settle(
        run: PrAiReviewRun, status: PrAiReviewRunStatus, *, error_code: str | None = None
    ) -> None:
        """Put a run into a terminal status with an end time.

        ``error_code`` is a stable machine string and nothing else. Provider
        messages, tracebacks and URLs never reach this column - the web panel
        reads it.
        """
        run.status = status
        run.error_code = error_code
        run.finished_at = utcnow()
        if run.started_at is None:
            run.started_at = run.finished_at


__all__: list[str] = ["CLAIM_BATCH_SIZE", "PrAiReviewRunService"]
