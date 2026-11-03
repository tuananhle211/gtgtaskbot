"""Payloads of the PR task codes ("điểm PR", 0085)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from meobot.api.schemas.board import DesignCodeItemResponse
from meobot.api.schemas.orders import DesignPickRequest
from meobot.api.schemas.units import DesignCodeResponse, TaskFormResponse
from meobot.application.board.sources import code_view
from meobot.application.performance.pr_points import ClaimView, ContentPoints, StageView
from meobot.domain.performance.work_points import STAGE_LABELS


class PrCatalogResponse(BaseModel):
    """ORD's live task codes per phase, and the forms - the PR picker's list."""

    phases: dict[str, list[DesignCodeResponse]]
    forms: list[TaskFormResponse]


class PrClaimResponse(BaseModel):
    id: uuid.UUID
    content_id: uuid.UUID
    content_code: str | None = None
    content_title: str | None = None
    stage: str
    stage_label: str
    user_id: uuid.UUID
    name: str
    #: ``{"THIET_KE": [...], "DUNG": [...]}``.
    items: dict[str, list[DesignCodeItemResponse]]
    points: int
    note: str | None
    #: PENDING = not counted yet (REJECTED: an old refusal, same), APPROVED =
    #: counted (the piece is done; ``decided_at`` = when).
    status: str
    submitted_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    return_reason: str | None
    decision_note: str | None

    @classmethod
    def from_domain(cls, view: ClaimView) -> PrClaimResponse:
        claim = view.claim
        return cls(
            id=claim.id,
            content_id=claim.content_id,
            content_code=view.content_code,
            content_title=view.content_title,
            stage=claim.stage,
            stage_label=STAGE_LABELS.get(claim.stage, claim.stage),
            user_id=claim.user_id,
            name=view.name,
            items={
                phase: [DesignCodeItemResponse.from_domain(code_view(item)) for item in items]
                for phase, items in view.items.items()
            },
            points=claim.points,
            note=claim.note,
            status=claim.status,
            submitted_at=claim.submitted_at,
            decided_by=view.decided_by,
            decided_at=claim.decided_at,
            return_reason=claim.return_reason,
            decision_note=claim.decision_note,
        )


class PrStageResponse(BaseModel):
    stage: str
    label: str
    phases: list[str]
    open: bool
    waiting: str | None
    may_claim: bool
    #: A Trưởng phòng PR: may edit / remove anybody's codes (until done).
    may_edit_others: bool = False
    claims: list[PrClaimResponse]

    @classmethod
    def from_domain(cls, view: StageView) -> PrStageResponse:
        return cls(
            stage=view.stage,
            label=view.label,
            phases=list(view.phases),
            open=view.open,
            waiting=view.waiting,
            may_claim=view.may_claim,
            may_edit_others=view.may_edit_others,
            claims=[PrClaimResponse.from_domain(claim) for claim in view.claims],
        )


class PrContentPointsResponse(BaseModel):
    content_id: uuid.UUID
    code: str
    title: str
    stages: list[PrStageResponse]
    may_review: bool
    #: The piece is done: codes frozen, points counted.
    done: bool = False

    @classmethod
    def from_domain(cls, view: ContentPoints) -> PrContentPointsResponse:
        return cls(
            content_id=view.content_id,
            code=view.code,
            title=view.title,
            stages=[PrStageResponse.from_domain(stage) for stage in view.stages],
            may_review=view.may_review,
            done=view.done,
        )


class PrClaimRequest(BaseModel):
    #: CONTENT / PRODUCTION.
    stage: str = Field(max_length=12)
    #: ``{"BIEN_TAP": [...]}`` / ``{"THIET_KE": [...], "DUNG": [...]}``.
    task_codes: dict[str, list[DesignPickRequest]]
    note: str | None = Field(default=None, max_length=1000)
    #: A Trưởng phòng PR editing somebody else's codes; none = one's own.
    user_id: uuid.UUID | None = None
