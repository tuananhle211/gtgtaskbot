"""Running one AI review: read the pinned draft, ask the model, settle the run.

Step 1F. This is the only place a provider is called on behalf of the PR module,
and it is deliberately the only place that knows both about a model and about a
workflow. Everything it does with the result goes through services that already
existed:

* the review row is appended by
  :meth:`~meobot.application.pr_ai_review_service.PrAiReviewService.record_review`,
  which locks the content, re-checks the stage and the version, writes
  ``pr_ai_reviews`` and applies the gating transition. This module does **not**
  write that table, does not assign ``workflow_stage`` and does not decide which
  stage follows a result;
* the execution's status is settled by
  :class:`~meobot.application.pr_ai_review_run_service.PrAiReviewRunService`;
* the outcome is derived by
  :func:`~meobot.domain.pr.ai_review.derive_outcome`, from finding severities.

```
claim (RUNNING) ─→ load pinned version ─→ provider ─→ validate ─→ derive outcome
                                                                      │
                    stale? ─→ SUPERSEDED, nothing transitions ◀───────┤
                                                                      │
                    record_review() ─→ pr_ai_reviews + stage change ──┘
                                        └─→ SUCCEEDED
```

Staleness, and what is kept
---------------------------

Between queueing and finishing, somebody may have rewritten the draft or a
reviewer may have moved the item. Before the result is recorded, the content is
locked and both facts are checked. If either moved, the run becomes
``SUPERSEDED`` carrying the outcome it derived, and **nothing transitions**.

No ``pr_ai_reviews`` row is written in that case, and that is a deliberate
choice rather than an oversight: ``record_review`` records reviews of the
current draft at ``AI_REVIEW`` and refuses anything else, and Step 1F does not
weaken a Step 1A1 invariant to make its own bookkeeping tidier. What the
execution concluded survives on the run row, which is where execution history
belongs.

Failure
-------

A provider failure leaves the content exactly where it was - in ``AI_REVIEW``,
with no review row and no transition. Transient failures are re-queued while
attempts remain; a malformed answer is treated as transient once, because a
second sample from the same model often validates. Nothing here retries a
business refusal: if the workflow says no, asking again will produce the same
no.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.pr_ai_review_run_service import PrAiReviewRunService
from meobot.application.pr_ai_review_service import PrAiReviewService, RecordAiReviewCommand
from meobot.application.pr_policy_pack_service import PrPolicyPackService
from meobot.application.pr_policy_selector import (
    DEFAULT_POLICY_CONTEXT_BUDGET,
    PolicyCoverageError,
    select_policy_context,
)
from meobot.core.errors import LLMError, MeoBotError
from meobot.core.logging import get_logger
from meobot.core.time import utcnow
from meobot.db.models.pr import PrBrand, PrChannel, PrContentItem, PrContentTarget
from meobot.db.models.pr_ai_review_run import PrAiReviewRun
from meobot.db.models.pr_content_version import PrContentVersion
from meobot.domain.identity.models import Actor
from meobot.domain.pr.ai_review import (
    PolicyContext,
    PrFullReviewOutput,
    PrPolicyCitationError,
    assert_citations_are_grounded,
    derive_outcome,
    full_review_json_schema,
    issues_payload,
    suggestions_payload,
)
from meobot.domain.pr.models import PrAiReviewResult, PrAiReviewRunStatus, PrWorkflowStage
from meobot.integrations.llm.base import LLMProvider, PrFullReviewRequest
from meobot.integrations.llm.pr_review_prompt import (
    FULL_REVIEW_SYSTEM_PROMPT,
    build_review_payload,
)

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    """What one execution attempt did, for the task body to log and return."""

    run_id: uuid.UUID
    status: PrAiReviewRunStatus
    outcome: PrAiReviewResult | None = None
    new_stage: PrWorkflowStage | None = None
    error_code: str | None = None
    will_retry: bool = False


class PrAiReviewExecutor:
    """Executes one claimed AI review run, end to end.

    Args:
        session: Unit of work. The caller owns the transaction boundary; the
            provider call happens **inside** it, which is acceptable here and
            not elsewhere because this is a background worker with no user
            waiting and its own bounded time limit.
        runs: Durable execution state.
        reviews: The append-only recorder, which also applies the gate.
        provider: The configured LLM. Never constructed here - the worker
            builds it once per task run, as every other task does.
    """

    def __init__(
        self,
        session: AsyncSession,
        runs: PrAiReviewRunService,
        reviews: PrAiReviewService,
        provider: LLMProvider,
        packs: PrPolicyPackService | None = None,
        policy_budget_chars: int = DEFAULT_POLICY_CONTEXT_BUDGET,
    ) -> None:
        self._session = session
        self._runs = runs
        self._reviews = reviews
        self._provider = provider
        self._packs = packs or PrPolicyPackService(session)
        self._policy_budget = policy_budget_chars

    async def execute(
        self, *, actor: Actor, request_id: uuid.UUID, run: PrAiReviewRun
    ) -> ExecutionOutcome:
        """Run one attempt. Never raises for an expected failure.

        Every branch settles the run, so a caller that gets a return value knows
        the row is not stranded ``RUNNING``. The only exceptions that escape are
        the unexpected ones, and the stale-claim sweeper collects those.
        """
        if run.status is not PrAiReviewRunStatus.RUNNING:
            # Somebody else settled it - a duplicate delivery, or a recovery
            # sweep that got there first. Doing nothing is the correct
            # response to being asked twice.
            logger.info(
                "ai_review_run_not_claimable",
                extra={"pr_ai_review_run_id": str(run.id), "status": run.status.value},
            )
            return ExecutionOutcome(run_id=run.id, status=run.status)

        version = await self._session.get(PrContentVersion, run.content_version_id)
        content = await self._session.get(PrContentItem, run.content_id)
        if version is None or content is None:
            await self._runs.mark_failed(run, error_code="content_missing")
            return ExecutionOutcome(
                run_id=run.id, status=PrAiReviewRunStatus.FAILED, error_code="content_missing"
            )

        logger.info(
            "ai_review_started",
            extra={
                "pr_ai_review_run_id": str(run.id),
                "pr_content_id": str(run.content_id),
                "pr_content_version_id": str(run.content_version_id),
                "attempt": run.attempt_count,
                "prompt_version": run.prompt_version,
            },
        )

        # Step 1F.1: the packs this run pinned when it was queued. Read from
        # ``pr_ai_review_run_policy_packs`` rather than resolved now, so a pack
        # activated while this run waited cannot change what it is judged
        # against. A legacy pre-1F.1 run has none and stays ungrounded.
        pinned = await self._policy_contexts(run)
        try:
            # Bounded here, deterministically, from the pinned packs only. The
            # packs stay complete in the database; this is what one prompt can
            # carry, and it is versioned so the choice stays explainable.
            selection = select_policy_context(
                pinned,
                content_terms=_content_terms(content, version),
                budget_chars=self._policy_budget,
            )
        except PolicyCoverageError as exc:
            # A pinned pack with no rules. Selecting an arbitrary handful would
            # produce a review that looks grounded and is not, so this fails
            # safely and the content stays in AI_REVIEW.
            await self._runs.mark_failed(run, error_code=_safe_error_code(exc))
            return ExecutionOutcome(
                run_id=run.id,
                status=PrAiReviewRunStatus.FAILED,
                error_code=_safe_error_code(exc),
            )
        contexts = selection.contexts

        started = utcnow()
        try:
            output = await self._ask(content, version, contexts)
            if contexts:
                # A citation the pinned pack does not contain is a hallucinated
                # policy claim. Raised as a ValueError so it travels the same
                # path a schema violation does and reaches the bounded retry -
                # never stripped and the rest of the finding kept.
                assert_citations_are_grounded(output, contexts)
        except PrPolicyCitationError:
            logger.warning(
                "ai_review_citation_rejected",
                extra={
                    "pr_ai_review_run_id": str(run.id),
                    "attempt": run.attempt_count,
                    "policy_packs": [context.pack_label for context in contexts],
                },
            )
            will_retry = await self._runs.requeue(run, error_code="invalid_policy_citation")
            return ExecutionOutcome(
                run_id=run.id,
                status=run.status,
                error_code="invalid_policy_citation",
                will_retry=will_retry,
            )
        except LLMError as exc:
            # Transient by assumption: a provider that is down, rate-limiting,
            # or returning something that did not validate. Another sample
            # often works, and the content is untouched either way.
            will_retry = await self._runs.requeue(run, error_code=_safe_error_code(exc))
            return ExecutionOutcome(
                run_id=run.id,
                status=run.status,
                error_code=_safe_error_code(exc),
                will_retry=will_retry,
            )

        outcome = derive_outcome(output)
        duration_ms = int((utcnow() - started).total_seconds() * 1000)

        stale = await self._staleness(run)
        if stale is not None:
            await self._runs.mark_superseded(
                run,
                outcome=outcome,
                model_name=self._provider.model,
                model_version=None,
                reason=stale,
            )
            return ExecutionOutcome(
                run_id=run.id,
                status=PrAiReviewRunStatus.SUPERSEDED,
                outcome=outcome,
                error_code=stale,
            )

        try:
            recorded = await self._reviews.record_review(
                actor=actor,
                request_id=request_id,
                command=RecordAiReviewCommand(
                    content_id=run.content_id,
                    reviewed_version=version.version_no,
                    review_type=run.review_type,
                    result=outcome,
                    model_name=self._provider.model,
                    prompt_version=run.prompt_version,
                    model_version=None,
                    reviewed_at=utcnow(),
                    # No score. A number derived from severities would read as
                    # a measurement and mean "one warning" - see
                    # ``meobot.domain.pr.ai_review``.
                    score=None,
                    summary=output.summary,
                    issues=issues_payload(output),
                    suggestions=suggestions_payload(output),
                    # The audit trail Step 1F.1 promised, without copying policy
                    # prose into ``pr_ai_reviews``: which selector chose which
                    # rules from which pinned pack.
                    policy_flags=[
                        {
                            "code": "policy_selection",
                            "severity": "INFO",
                            "selector_version": selection.selector_version,
                            "packs": [context.pack_label for context in contexts],
                            "selected_rule_ids": list(selection.selected_rule_ids),
                        }
                    ]
                    if contexts
                    else [],
                ),
            )
        except MeoBotError as exc:
            # A business refusal, from the recorder or the matrix. Not
            # retryable: asking again produces the same refusal, and the
            # content is safely where it was.
            await self._runs.mark_failed(run, error_code=_safe_error_code(exc))
            return ExecutionOutcome(
                run_id=run.id,
                status=PrAiReviewRunStatus.FAILED,
                outcome=outcome,
                error_code=_safe_error_code(exc),
            )

        await self._runs.mark_succeeded(
            run,
            review_id=recorded.review.id,
            outcome=outcome,
            model_name=self._provider.model,
            model_version=None,
        )
        logger.info(
            "ai_review_completed",
            extra={
                "pr_ai_review_run_id": str(run.id),
                "pr_content_id": str(run.content_id),
                "pr_content_version_id": str(run.content_version_id),
                "attempt": run.attempt_count,
                "model_name": self._provider.model,
                "prompt_version": run.prompt_version,
                "duration_ms": duration_ms,
                "selector_version": selection.selector_version,
                "policy_rules_selected": len(selection.selected_rule_ids),
                "policy_rules_available": selection.available_rule_count,
                "outcome": outcome.value,
                "new_stage": recorded.new_stage.value if recorded.new_stage else None,
                "findings": len(output.findings),
            },
        )
        return ExecutionOutcome(
            run_id=run.id,
            status=PrAiReviewRunStatus.SUCCEEDED,
            outcome=outcome,
            new_stage=recorded.new_stage,
        )

    # --- Internals --------------------------------------------------------
    async def _policy_contexts(self, run: PrAiReviewRun) -> tuple[PolicyContext, ...]:
        """The pinned packs, as reference data. **No network, ever.**

        This module does not import the policy fetcher and nothing here resolves
        a URL. Everything the review sees about platform policy came out of the
        database, from a pack an operator activated before this run was queued.
        """
        pins = await self._runs.pinned_packs(run.id)
        contexts: list[PolicyContext] = []
        for pin in pins:
            pack = await self._packs.get_pack(pin.policy_pack_id)
            if pack is None:
                continue
            contexts.append(await self._packs.context_for(pack))
        return tuple(contexts)

    async def _ask(
        self,
        content: PrContentItem,
        version: PrContentVersion,
        contexts: Sequence[PolicyContext] = (),
    ) -> PrFullReviewOutput:
        """Build the payload from the database and ask the provider.

        Nothing in the payload comes from a client. The script under review is
        read from the pinned immutable version row, so a browser cannot have a
        passing review written about text that was never stored.
        """
        brand = await self._session.get(PrBrand, content.brand_id)
        channels = await self._channel_names(content.id)
        payload = build_review_payload(
            content_code=content.code,
            title=version.title,
            version_no=version.version_no,
            brand=brand.name if brand else None,
            channels=channels,
            content_format=None,
            pillar=None,
            topic=version.topic,
            hook=version.hook,
            brief=version.brief,
            script_text=version.script_text,
            policy_contexts=contexts,
        )
        return await self._provider.review_pr_content(
            PrFullReviewRequest(
                system_prompt=FULL_REVIEW_SYSTEM_PROMPT,
                payload=payload,
                json_schema=full_review_json_schema(),
                # The offline provider answers from these; the real one ignores
                # them. Never the authoritative copy of anything.
                context={"script_text": version.script_text or "", "hook": version.hook or ""},
            )
        )

    async def _channel_names(self, content_id: uuid.UUID) -> list[str]:
        """The planned channels' names, in one join. No query per target."""
        result = await self._session.execute(
            select(PrChannel.name)
            .join(PrContentTarget, PrContentTarget.channel_id == PrChannel.id)
            .where(PrContentTarget.content_id == content_id)
            .order_by(PrChannel.name.asc())
        )
        return list(result.scalars().all())

    async def _staleness(self, run: PrAiReviewRun) -> str | None:
        """Why this result may no longer be applied, or ``None``.

        Checked under the content row's lock, and the lock is held from here
        until the caller's transaction ends - so the stage and the version
        cannot move between this check and ``record_review``'s own.
        """
        content = await self._reviews.lock_content(run.content_id)
        if content.workflow_stage is not PrWorkflowStage.AI_REVIEW:
            return "stage_moved"
        current = await self._session.execute(
            select(PrContentVersion.id)
            .where(PrContentVersion.content_id == run.content_id)
            .order_by(PrContentVersion.version_no.desc())
            .limit(1)
        )
        if current.scalars().first() != run.content_version_id:
            return "version_superseded"
        return None


def _content_terms(content: PrContentItem, version: PrContentVersion) -> list[str]:
    """The words relevance scoring matches policy rules against.

    Drawn from the draft itself - title, topic, hook, brief, script - so the
    signal is the content under review and nothing a caller supplied.
    """
    parts = [
        content.title,
        version.title,
        version.topic or "",
        version.hook or "",
        version.brief or "",
        version.script_text or "",
    ]
    return " ".join(parts).split()


def _safe_error_code(error: MeoBotError) -> str:
    """A stable machine string, bounded, never a provider message.

    ``MeoBotError.code`` is already a code rather than prose, but this is the
    value that reaches the database column and the browser, so it is truncated
    to the column's width here rather than trusted to be short.
    """
    return (error.code or "unknown_error")[:64]


__all__: list[str] = ["ExecutionOutcome", "PrAiReviewExecutor"]
