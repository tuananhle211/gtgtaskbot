"""Recording what an automated review said, and letting exactly one kind of it move work.

This service writes ``pr_ai_reviews`` rows and, for one review type only, asks
:class:`~meobot.application.pr_workflow_service.PrContentWorkflowService` to
move the content. It contains no model call, no prompt and no client - Step 1C
records verdicts that something else produced, exactly as Step 1A1 built the
table before anything could fill it.

Three rules, and each is the answer to a specific way this could go wrong.

**The verdict belongs to the draft it judged.** ``reviewed_version`` must equal
the current version of the content. A review submitted against version 4 while
version 5 exists is refused, because the only thing that review is evidence
about is text nobody is looking at any more. This is also what stops a stale
``PASS`` from authorising a later draft: the PASS is stored against 4, the
content is at 5, and the gate reads the current version.

**Only ``FULL_REVIEW`` is a gate.** ``SCRIPT_QUALITY``, ``POLICY_COMPLIANCE``
and ``BRAND_TONE`` are stored, are visible to the human reviewer through
:class:`~meobot.application.pr_query_service.PrQueryService`, and move nothing.
A brand-tone check that dislikes a phrase is advice; treating it as a gate
would mean an automated stylistic opinion could hold up publication with no
human in the loop.

**Nothing here writes an approval.** There is no code path in this module that
touches ``pr_approval_events``, and the workflow matrix has no edge from
``AI_REVIEW`` to ``APPROVED`` for any trigger, so a verdict cannot reach the
approved state even by asking for it. The furthest a ``PASS`` gets is handing
the work to a person.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.pr_content_service import PrContentService
from meobot.application.pr_support import record_pr_event
from meobot.application.pr_workflow_service import PrContentWorkflowService
from meobot.core.logging import get_logger
from meobot.db.models.pr import PrContentItem, PrTask
from meobot.db.models.pr_ai_review import PrAiReview
from meobot.domain.audit.models import AuditAction
from meobot.domain.identity.models import Actor
from meobot.domain.pr.errors import (
    PrNotFoundError,
    PrPermissionDeniedError,
    PrReviewVersionMismatchError,
    PrValidationError,
    PrWorkflowTransitionError,
)
from meobot.domain.pr.models import PrAiReviewResult, PrAiReviewType, PrWorkflowStage
from meobot.domain.pr.policy import PR_AI_REVIEW_PERMISSION, require_permission
from meobot.domain.pr.workflow import PrTransitionTrigger, ai_review_target, is_gating_review

logger = get_logger(__name__)

#: The bounds ``ck_pr_ai_reviews_score_in_range`` enforces, checked here so the
#: failure is a typed domain error instead of a driver exception.
MIN_SCORE = Decimal(0)
MAX_SCORE = Decimal(100)


@dataclass(frozen=True, slots=True)
class RecordAiReviewCommand:
    """One completed automated review of one draft.

    ``model_name`` and ``prompt_version`` are required and non-empty for the
    reason Step 1A1 gave: findings from a model that has since been replaced,
    or a prompt that has since been rewritten, mean something different from
    today's, and a verdict that cannot say what produced it has no standing.
    """

    content_id: uuid.UUID
    reviewed_version: int
    review_type: PrAiReviewType
    result: PrAiReviewResult
    model_name: str
    prompt_version: str
    reviewed_at: datetime
    score: Decimal | None = None
    summary: str | None = None
    issues: list[Any] | None = None
    suggestions: list[Any] | None = None
    policy_flags: list[Any] | None = None
    model_version: str | None = None
    task_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class AiReviewOutcome:
    """What was recorded, and what it did to the content."""

    review: PrAiReview
    #: ``None`` when the review type is not a gate, which is the normal case
    #: for three of the four types. Distinct from "the stage did not change":
    #: a gating review always changes it.
    new_stage: PrWorkflowStage | None

    @property
    def gated(self) -> bool:
        return self.new_stage is not None


class PrAiReviewService:
    """Appends AI review history and applies the one gate that follows from it.

    Args:
        session: Unit of work. The caller owns the transaction boundary.
        audit: Event writer sharing that session.
        content: Reader for the current version.
        workflow: The only writer of ``workflow_stage``.
    """

    def __init__(
        self,
        session: AsyncSession,
        audit: AuditService,
        content: PrContentService,
        workflow: PrContentWorkflowService,
    ) -> None:
        self._session = session
        self._audit = audit
        self._content = content
        self._workflow = workflow

    async def record_review(
        self, *, actor: Actor, request_id: uuid.UUID, command: RecordAiReviewCommand
    ) -> AiReviewOutcome:
        """Store one verdict, and move the content if it is a gating one.

        The content row is locked before the version is read, so the version
        this review is bound to cannot change between the check and the write.

        Raises:
            PrPermissionDeniedError: The actor may not record AI reviews.
            PrNotFoundError: No such content, or no such task.
            PrWorkflowTransitionError: The content is not at ``AI_REVIEW``.
            PrReviewVersionMismatchError: The review judged a stale draft.
            PrValidationError: Provenance is blank, or the score is out of range.
        """
        # A permission, not a capability: an AI review names a model rather
        # than a person, so there is no individual to grant anything to.
        require_permission(actor, PR_AI_REVIEW_PERMISSION)

        model_name = self._require_text(command.model_name, "model_name")
        prompt_version = self._require_text(command.prompt_version, "prompt_version")
        self._require_score_in_range(command.score)

        content = await self._workflow.lock(command.content_id)
        if content.workflow_stage is not PrWorkflowStage.AI_REVIEW:
            raise PrWorkflowTransitionError(
                "PR content is not awaiting AI review",
                details={
                    "content_id": str(content.id),
                    "current": content.workflow_stage.value,
                    "required": PrWorkflowStage.AI_REVIEW.value,
                },
            )

        current = await self._content.require_current_version(content.id)
        if command.reviewed_version != current.version_no:
            raise PrReviewVersionMismatchError(
                "The AI review judged a draft that is no longer current",
                details={
                    "content_id": str(content.id),
                    "reviewed_version": command.reviewed_version,
                    "current_version": current.version_no,
                },
            )

        if command.task_id is not None and await self._session.get(PrTask, command.task_id) is None:
            raise PrNotFoundError(
                "No PR task with that id", details={"task_id": str(command.task_id)}
            )

        review = PrAiReview(
            content_id=content.id,
            task_id=command.task_id,
            review_type=command.review_type,
            reviewed_version=command.reviewed_version,
            result=command.result,
            score=command.score,
            summary=command.summary,
            issues=command.issues,
            suggestions=command.suggestions,
            policy_flags=command.policy_flags,
            model_name=model_name,
            model_version=command.model_version,
            prompt_version=prompt_version,
            reviewed_at=command.reviewed_at,
        )
        self._session.add(review)
        await self._session.flush()

        new_stage: PrWorkflowStage | None = None
        if is_gating_review(command.review_type):
            new_stage = ai_review_target(command.result)
            await self._workflow.apply(
                actor=actor,
                request_id=request_id,
                content=content,
                target=new_stage,
                trigger=PrTransitionTrigger.AI_REVIEW,
                reason=f"ai_review:{command.result.value}",
            )

        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_AI_REVIEW_RECORDED,
            entity_type="pr_ai_review",
            entity_id=review.id,
            after={
                "content_id": str(content.id),
                "content_code": content.code,
                "review_type": command.review_type.value,
                "result": command.result.value,
                "reviewed_version": command.reviewed_version,
                "score": str(command.score) if command.score is not None else None,
                "model_name": model_name,
                "model_version": command.model_version,
                "prompt_version": prompt_version,
                "gating": new_stage is not None,
                "new_stage": new_stage.value if new_stage else None,
            },
        )
        logger.info(
            "pr_ai_review_recorded",
            extra={
                "pr_content_id": str(content.id),
                "review_type": command.review_type.value,
                "result": command.result.value,
                "reviewed_version": command.reviewed_version,
                "gating": new_stage is not None,
            },
        )
        return AiReviewOutcome(review=review, new_stage=new_stage)

    def require_may_request(self, actor: Actor) -> None:
        """Raise unless this actor may ask for an automated review.

        Step 1F. The *same* permission recording one takes, and deliberately
        not a new capability: "may cause an AI review to happen" and "may record
        what one said" are the same authority over the same subsystem, and
        inventing a second name for it would have meant a grant nobody
        administers and a migration nobody needed.
        """
        require_permission(actor, PR_AI_REVIEW_PERMISSION)

    async def may_request(self, actor: Actor) -> bool:
        """The same decision as :meth:`require_may_request`, as a boolean.

        For a client deciding whether to draw the retry button. Never used
        instead of the check at the write - the retry route calls the raising
        version.
        """
        try:
            self.require_may_request(actor)
        except PrPermissionDeniedError:
            return False
        return True

    async def lock_content(self, content_id: uuid.UUID) -> PrContentItem:
        """Take the content row's lock, for a caller about to record a review.

        Step 1F. The executor checks staleness before recording, and that check
        is only worth anything if the stage and the version cannot move between
        it and the write - so the check must happen under the same lock the
        write takes. Delegates to ``PrContentWorkflowService.lock``: there is
        one locking implementation, and it is the one the transition uses.
        """
        return await self._workflow.lock(content_id)

    # --- Reading ----------------------------------------------------------
    async def latest_gating_review(
        self, content_id: uuid.UUID, *, version_no: int
    ) -> PrAiReview | None:
        """The newest ``FULL_REVIEW`` for exactly this draft, if there is one.

        Bound to ``version_no`` rather than "the newest FULL_REVIEW of this
        content" on purpose. That is the query that makes version safety
        checkable: after a rewrite, the previous draft's PASS is still on file
        and still findable, and this returns nothing for the new draft until
        the new draft has been reviewed.
        """
        result = await self._session.execute(
            select(PrAiReview)
            .where(
                PrAiReview.content_id == content_id,
                PrAiReview.reviewed_version == version_no,
                PrAiReview.review_type == PrAiReviewType.FULL_REVIEW,
            )
            .order_by(PrAiReview.reviewed_at.desc(), PrAiReview.created_at.desc())
            .limit(1)
        )
        return result.scalars().one_or_none()

    async def list_reviews(
        self, content_id: uuid.UUID, *, version_no: int | None = None, limit: int = 50
    ) -> list[PrAiReview]:
        """Review history for one item, newest first, optionally one draft only."""
        statement = select(PrAiReview).where(PrAiReview.content_id == content_id)
        if version_no is not None:
            statement = statement.where(PrAiReview.reviewed_version == version_no)
        statement = statement.order_by(
            PrAiReview.reviewed_at.desc(), PrAiReview.created_at.desc()
        ).limit(limit)
        result = await self._session.execute(statement)
        return list(result.scalars().all())

    # --- Guards -----------------------------------------------------------
    @staticmethod
    def _require_text(value: str, field_name: str) -> str:
        text = (value or "").strip()
        if not text:
            raise PrValidationError(
                f"AI review {field_name} must not be blank", details={"field": field_name}
            )
        return text

    @staticmethod
    def _require_score_in_range(score: Decimal | None) -> None:
        if score is None:
            return
        if score < MIN_SCORE or score > MAX_SCORE:
            raise PrValidationError(
                "AI review score must be between 0 and 100",
                details={"score": str(score), "min": str(MIN_SCORE), "max": str(MAX_SCORE)},
            )


__all__: list[str] = ["AiReviewOutcome", "PrAiReviewService", "RecordAiReviewCommand"]
