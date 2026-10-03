"""The monthly manager review: three judgements, once, per person. M6.

**One row per employee per month.** Not one per work item, not one per content
piece, not one per contribution - and the unique constraint says so, so the
product decision cannot be got wrong by a later caller. Twenty people is twenty
forms a month. The same model applied per deliverable would be two thousand,
which is a system nobody fills in and therefore a system that scores nothing.

What the manager is asked, and what they are not
--------------------------------------------------

Three questions about the **month**: how was the quality of this person's output,
how did they manage progress and deadlines, and what did they contribute to the
department's and the business's shared results.

They are not asked to score individual deliverables, and the system does not
compute any of the three for them. Deadline data, overdue counts and submission
timestamps are shown as **evidence** beside the question - see
:mod:`meobot.application.pr_performance_service` - and they never become the
answer. The editor whose cut was late because a doctor moved a shoot is the case
that decides this: the system can see the late task and cannot see the reason,
and a rule that scored the first without the second would be confidently wrong
every time it mattered.

Đạt is exactly 100
-------------------

A person who did what the role expects is neither rewarded nor punished by the
review terms. That is what makes the workload term meaningful: the index moves
because of what somebody produced unless a manager deliberately says otherwise,
in writing.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.pr_capability_service import PrCapabilityService
from meobot.application.pr_support import lock_row, record_pr_event
from meobot.core.time import utcnow
from meobot.db.models.pr_performance import PrPerformancePolicy, PrPerformanceReview
from meobot.db.models.pr_reporting import PrReportingPeriod
from meobot.domain.audit.models import AuditAction
from meobot.domain.identity.models import Actor
from meobot.domain.pr.errors import (
    PrConflictError,
    PrNotFoundError,
    PrPermissionDeniedError,
    PrValidationError,
)
from meobot.domain.pr.performance import (
    DEFAULT_BUSINESS_CONTRIBUTION_SCORES,
    DEFAULT_QUALITY_SCORES,
    DEFAULT_TIMELINESS_SCORES,
    PrPerformanceLevel,
    PrPerformanceReviewDimension,
    note_required,
)
from meobot.domain.pr.policy import PrCapability
from meobot.domain.pr.reporting import PrPeriodStatus

#: Which barem each dimension reads, and which columns it lives in. One table so
#: the three dimensions cannot drift into three slightly different code paths.
_DIMENSIONS: dict[PrPerformanceReviewDimension, tuple[str, dict[PrPerformanceLevel, Decimal]]] = {
    PrPerformanceReviewDimension.QUALITY: ("quality", dict(DEFAULT_QUALITY_SCORES)),
    PrPerformanceReviewDimension.TIMELINESS: ("timeliness", dict(DEFAULT_TIMELINESS_SCORES)),
    PrPerformanceReviewDimension.BUSINESS_CONTRIBUTION: (
        "business_contribution",
        dict(DEFAULT_BUSINESS_CONTRIBUTION_SCORES),
    ),
}

#: Audit action per dimension. Separate names because *"who changed my timeliness
#: from Đạt to Chưa đạt"* is a question about one dimension, and a shared event
#: would make answering it a diff of a JSON blob.
_RATED_ACTIONS: dict[PrPerformanceReviewDimension, AuditAction] = {
    PrPerformanceReviewDimension.QUALITY: AuditAction.PR_PERFORMANCE_QUALITY_RATED,
    PrPerformanceReviewDimension.TIMELINESS: AuditAction.PR_PERFORMANCE_TIMELINESS_RATED,
    PrPerformanceReviewDimension.BUSINESS_CONTRIBUTION: (
        AuditAction.PR_PERFORMANCE_CONTRIBUTION_RATED
    ),
}


@dataclass(frozen=True, slots=True)
class DimensionRating:
    """One dimension's answer: a rung, and the sentence explaining it."""

    level: PrPerformanceLevel
    note: str | None = None


class PrPerformanceReviewService:
    """Creates and revises the monthly review. ``PR_PERFORMANCE_REVIEW``."""

    def __init__(
        self,
        session: AsyncSession,
        audit: AuditService,
        capabilities: PrCapabilityService,
    ) -> None:
        self._session = session
        self._audit = audit
        self._capabilities = capabilities

    async def review_for(
        self, *, user_id: uuid.UUID, period_id: uuid.UUID
    ) -> PrPerformanceReview | None:
        """The month's review, or ``None``. No capability: the calculation needs it."""
        return (
            (
                await self._session.execute(
                    select(PrPerformanceReview).where(
                        PrPerformanceReview.user_id == user_id,
                        PrPerformanceReview.reporting_period_id == period_id,
                    )
                )
            )
            .scalars()
            .one_or_none()
        )

    async def read(
        self, *, actor: Actor, user_id: uuid.UUID, period_id: uuid.UUID
    ) -> PrPerformanceReview | None:
        """Read one review. **Your own, or anybody's if you may review.**

        An employee seeing the judgement made about them is the point of writing
        it down; an employee seeing a colleague's is a different act and needs
        the reviewing capability.
        """
        if actor.user_id != user_id:
            await self._capabilities.require(actor, PrCapability.PR_PERFORMANCE_REVIEW)
        return await self.review_for(user_id=user_id, period_id=period_id)

    async def submit(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        user_id: uuid.UUID,
        period_id: uuid.UUID,
        quality: DimensionRating | None = None,
        timeliness: DimensionRating | None = None,
        business_contribution: DimensionRating | None = None,
        overall_note: str | None = None,
    ) -> PrPerformanceReview:
        """Record or revise the month's judgements. ``PR_PERFORMANCE_REVIEW``.

        **One call, three dimensions**, and every one of them optional: a manager
        who rates quality on Tuesday and contribution on Friday is doing the
        ordinary thing, and the review sits half-filled in between. A missing
        dimension is *missing* - the calculation reports
        ``PERFORMANCE_REVIEW_PENDING`` and never defaults it to 100.

        Three refusals, and each is a rule rather than a validation:

        * **self-review**, whoever the actor is. An owner rating their own month
          is the one failure that would invalidate the whole exercise, so it is
          refused here and by a database CHECK;
        * **a closed or locked period.** Agreed numbers do not move, and there is
          no force flag;
        * **any rung but *Đạt* without a note.** The result follows a person.
        """
        await self._capabilities.require(actor, PrCapability.PR_PERFORMANCE_REVIEW)
        if actor.user_id is not None and actor.user_id == user_id:
            raise PrPermissionDeniedError(
                "Không thể tự đánh giá hiệu suất của chính mình.",
                details={"reason": "self_review", "user_id": str(user_id)},
            )

        period = await self._require_open_period(period_id)
        ratings = {
            PrPerformanceReviewDimension.QUALITY: quality,
            PrPerformanceReviewDimension.TIMELINESS: timeliness,
            PrPerformanceReviewDimension.BUSINESS_CONTRIBUTION: business_contribution,
        }
        for dimension, rating in ratings.items():
            if (
                rating is not None
                and note_required(rating.level)
                and not (rating.note or "").strip()
            ):
                raise PrValidationError(
                    "Mức đánh giá khác “Đạt” bắt buộc phải có nhận xét.",
                    details={
                        "field": f"{_DIMENSIONS[dimension][0]}_note",
                        "reason": "note_required_for_non_default_level",
                        "dimension": dimension.value,
                        "level": rating.level.value,
                    },
                )

        review = await self.review_for(user_id=user_id, period_id=period.id)
        if review is None:
            review = PrPerformanceReview(user_id=user_id, reporting_period_id=period.id)
            self._session.add(review)
            await self._session.flush()
            await record_pr_event(
                self._audit,
                request_id=request_id,
                actor=actor,
                action=AuditAction.PR_PERFORMANCE_REVIEW_CREATED,
                entity_type="pr_performance_review",
                entity_id=review.id,
                after={"user_id": str(user_id), "period_code": period.code},
            )
        else:
            # Held for the duration, because two managers rating the same person
            # at once must not interleave into a half-written review.
            locked = await lock_row(self._session, PrPerformanceReview, review.id)
            if locked is not None:
                review = locked

        for dimension, rating in ratings.items():
            if rating is None:
                continue
            await self._apply(
                actor=actor,
                request_id=request_id,
                review=review,
                period=period,
                dimension=dimension,
                rating=rating,
            )

        if overall_note is not None:
            review.overall_note = overall_note.strip() or None
        review.reviewer_user_id = actor.user_id
        review.reviewed_at = utcnow()
        await self._session.flush()
        return review

    async def _apply(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        review: PrPerformanceReview,
        period: PrReportingPeriod,
        dimension: PrPerformanceReviewDimension,
        rating: DimensionRating,
    ) -> None:
        """Write one dimension, and audit it **only when it actually moved**.

        Re-saving a form without changing a rung is not a decision, and a trail
        that recorded it would bury the changes somebody searches this table for.
        """
        prefix, barem = _DIMENSIONS[dimension]
        score = barem[rating.level]
        before_level = getattr(review, f"{prefix}_level")
        before_score = getattr(review, f"{prefix}_score")
        before_note = getattr(review, f"{prefix}_note")
        note = (rating.note or "").strip() or None

        setattr(review, f"{prefix}_level", rating.level)
        setattr(review, f"{prefix}_score", score)
        setattr(review, f"{prefix}_note", note)
        await self._session.flush()

        if before_level is rating.level and before_note == note:
            return
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=_RATED_ACTIONS[dimension],
            entity_type="pr_performance_review",
            entity_id=review.id,
            before={
                "level": before_level.value if before_level else None,
                "score": str(before_score) if before_score is not None else None,
                "note": before_note,
            },
            after={
                "level": rating.level.value,
                "score": str(score),
                "note": note,
                "user_id": str(review.user_id),
                "period_code": period.code,
            },
        )

    async def _require_open_period(self, period_id: uuid.UUID) -> PrReportingPeriod:
        period = await self._session.get(PrReportingPeriod, period_id)
        if period is None:
            raise PrNotFoundError(
                "Không tìm thấy kỳ báo cáo.",
                details={"field": "period_id", "reason": "period_not_found"},
            )
        if period.status is not PrPeriodStatus.OPEN:
            raise PrConflictError(
                "Kỳ báo cáo đã đóng nên không sửa được đánh giá.",
                details={
                    "field": "reporting_period_id",
                    "reason": "period_not_open",
                    "status": period.status.value,
                },
            )
        return period

    def policy_scores(self, policy: PrPerformancePolicy | None = None) -> dict[str, dict[str, str]]:
        """The barems as a client should render them. Defaults until a policy is approved."""
        if policy is None:
            return {
                "quality": {k.value: str(v) for k, v in DEFAULT_QUALITY_SCORES.items()},
                "timeliness": {k.value: str(v) for k, v in DEFAULT_TIMELINESS_SCORES.items()},
                "business_contribution": {
                    k.value: str(v) for k, v in DEFAULT_BUSINESS_CONTRIBUTION_SCORES.items()
                },
            }
        return {
            "quality": dict(policy.quality_scores),
            "timeliness": dict(policy.timeliness_scores),
            "business_contribution": dict(policy.business_contribution_scores),
        }


__all__: list[str] = ["DimensionRating", "PrPerformanceReviewService"]
