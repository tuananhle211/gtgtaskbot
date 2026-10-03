"""Script, version, review and approval schemas."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from meobot.application.script_service import ScriptDetail
from meobot.db.models.script import Script, ScriptVersion
from meobot.db.models.script_approval import ScriptApproval
from meobot.db.models.script_review import ScriptReview


class ScriptResponse(BaseModel):
    """A script as exposed by the API."""

    id: uuid.UUID
    external_script_id: str
    sheet_profile_id: uuid.UUID | None
    script_type_id: uuid.UUID | None
    status: str
    author: str | None
    deadline: date | None
    channel: str | None
    source_row_number: int | None
    current_version_id: uuid.UUID | None
    current_version_number: int | None
    title: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, model: Script) -> ScriptResponse:
        """Map an ORM row onto the wire schema."""
        version = model.current_version
        return cls(
            id=model.id,
            external_script_id=model.external_script_id,
            sheet_profile_id=model.sheet_profile_id,
            script_type_id=model.script_type_id,
            status=model.status.value,
            author=model.author,
            deadline=model.deadline,
            channel=model.channel,
            source_row_number=model.source_row_number,
            current_version_id=model.current_version_id,
            current_version_number=version.version_number if version else None,
            title=version.title if version else None,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )


class ScriptVersionResponse(BaseModel):
    """One immutable snapshot of a script's content."""

    id: uuid.UUID
    script_id: uuid.UUID
    version_number: int
    title: str
    hook: str | None
    script_body: str
    production_notes: str | None
    source_hash: str
    source_row_number: int | None
    created_at: datetime

    @classmethod
    def from_model(cls, model: ScriptVersion) -> ScriptVersionResponse:
        return cls(
            id=model.id,
            script_id=model.script_id,
            version_number=model.version_number,
            title=model.title,
            hook=model.hook,
            script_body=model.script_body,
            production_notes=model.production_notes,
            source_hash=model.source_hash,
            source_row_number=model.source_row_number,
            created_at=model.created_at,
        )


class ScriptReviewResponse(BaseModel):
    """One stored AI review."""

    id: uuid.UUID
    script_id: uuid.UUID
    script_version_id: uuid.UUID
    script_type_version_id: uuid.UUID | None
    provider: str
    model: str
    overall_score: int
    verdict: str
    summary: str
    strengths: list[str]
    critical_issues: list[str]
    recommendations: list[str]
    revised_hook_suggestion: str | None
    structured_response: dict[str, Any]
    usage_metadata: dict[str, Any]
    created_at: datetime

    @classmethod
    def from_model(cls, model: ScriptReview) -> ScriptReviewResponse:
        return cls(
            id=model.id,
            script_id=model.script_id,
            script_version_id=model.script_version_id,
            script_type_version_id=model.script_type_version_id,
            provider=model.provider,
            model=model.model,
            overall_score=model.overall_score,
            verdict=model.verdict.value,
            summary=model.summary,
            strengths=_as_str_list(model.strengths),
            critical_issues=_as_str_list(model.critical_issues),
            recommendations=_as_str_list(model.recommendations),
            revised_hook_suggestion=model.revised_hook_suggestion,
            structured_response=dict(model.structured_response),
            usage_metadata=dict(model.usage_metadata),
            created_at=model.created_at,
        )


class ScriptApprovalResponse(BaseModel):
    """One human decision on one exact version."""

    id: uuid.UUID
    script_id: uuid.UUID
    script_version_id: uuid.UUID
    action: str
    status_before: str
    status_after: str
    actor_user_id: uuid.UUID | None
    actor_telegram_id: int | None
    comment: str | None
    created_at: datetime
    #: Spelled out so an API consumer cannot mistake production approval for
    #: publishing permission (ADR-002).
    grants_publishing: bool = False

    @classmethod
    def from_model(cls, model: ScriptApproval) -> ScriptApprovalResponse:
        return cls(
            id=model.id,
            script_id=model.script_id,
            script_version_id=model.script_version_id,
            action=model.action.value,
            status_before=model.status_before.value,
            status_after=model.status_after.value,
            actor_user_id=model.actor_user_id,
            actor_telegram_id=model.actor_telegram_id,
            comment=model.comment,
            created_at=model.created_at,
            grants_publishing=False,
        )


class ScriptDetailResponse(BaseModel):
    """A script together with its current version and latest review."""

    script: ScriptResponse
    current_version: ScriptVersionResponse | None
    latest_review: ScriptReviewResponse | None
    review_matches_current_version: bool

    @classmethod
    def from_detail(cls, detail: ScriptDetail) -> ScriptDetailResponse:
        return cls(
            script=ScriptResponse.from_model(detail.script),
            current_version=(
                ScriptVersionResponse.from_model(detail.version) if detail.version else None
            ),
            latest_review=(
                ScriptReviewResponse.from_model(detail.review) if detail.review else None
            ),
            review_matches_current_version=detail.review_matches_version,
        )


class ScriptListResponse(BaseModel):
    """Envelope for script listings."""

    items: list[ScriptResponse]
    total: int


class ApproveProductionRequest(BaseModel):
    """Body for ``POST /scripts/{id}/approve-production``."""

    model_config = ConfigDict(extra="forbid")

    expected_version_id: uuid.UUID | None = Field(
        default=None,
        description="Refuse the approval when this is no longer the current version.",
    )
    comment: str | None = Field(default=None, max_length=1000)
    require_review: bool = Field(
        default=True,
        description="Refuse when the current version has never been reviewed.",
    )


class RequestRevisionRequest(BaseModel):
    """Body for ``POST /scripts/{id}/request-revision``."""

    model_config = ConfigDict(extra="forbid")

    comment: str | None = Field(default=None, max_length=2000)
    expected_version_id: uuid.UUID | None = None


def _as_str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    return []
