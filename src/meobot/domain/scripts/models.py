"""Script value objects: content identity, review results, approval actions.

Pure domain: no SQLAlchemy, no HTTP, no vendor SDK. The LLM's review output is
described here as a Pydantic model so it can be validated *before* anything is
stored, and so the same schema can be handed to a provider as a JSON schema.
"""

from __future__ import annotations

import hashlib
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

#: Fields whose change makes a script a genuinely new version. Anything else
#: (author, deadline, the sheet's own status column) may drift without forcing
#: a re-review.
CONTENT_FIELDS: tuple[str, ...] = ("title", "hook", "script_body", "production_notes")


class ReviewVerdict(StrEnum):
    """The reviewer's overall recommendation. Advisory only - a human decides."""

    APPROVE = "approve"
    MINOR_REVISION = "minor_revision"
    MAJOR_REVISION = "major_revision"
    REJECT = "reject"


class ApprovalAction(StrEnum):
    """What a human did to a specific :class:`ScriptVersion`.

    ``APPROVE_FOR_PRODUCTION`` authorises filming and nothing else. There is
    deliberately no ``APPROVE_FOR_PUBLISH`` here: publishing permission belongs
    to the video workflow (ADR-002).
    """

    APPROVE_FOR_PRODUCTION = "approve_for_production"
    REQUEST_REVISION = "request_revision"


def compute_source_hash(
    *,
    title: str,
    hook: str | None,
    script_body: str,
    production_notes: str | None,
) -> str:
    """Stable hash over the fields that define a script's content.

    Whitespace is collapsed so a cosmetic edit in Google Sheets (a trailing
    space, a re-wrapped line) does not create a new version, while any real
    wording change does.
    """
    parts = [
        _canonical(title),
        _canonical(hook),
        _canonical(script_body),
        _canonical(production_notes),
    ]
    return hashlib.sha256("␟".join(parts).encode("utf-8")).hexdigest()


def _canonical(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(value.split())


class ScriptReviewResult(BaseModel):
    """Structured output of one AI review.

    Every field is bounded: an LLM that returns a 50 000-character 'summary'
    fails validation instead of filling the database.
    """

    model_config = ConfigDict(extra="ignore")

    overall_score: int = Field(ge=0, le=100)
    verdict: ReviewVerdict
    short_summary_for_telegram: str = Field(min_length=1, max_length=600)
    strengths: list[str] = Field(default_factory=list, max_length=10)
    critical_issues: list[str] = Field(default_factory=list, max_length=10)
    improvement_recommendations: list[str] = Field(default_factory=list, max_length=10)
    hook_analysis: str = Field(default="", max_length=2000)
    structure_analysis: str = Field(default="", max_length=2000)
    audience_fit: str = Field(default="", max_length=2000)
    brand_safety_notes: str = Field(default="", max_length=2000)
    factual_risk_notes: str = Field(default="", max_length=2000)
    medical_risk_notes: str = Field(default="", max_length=2000)
    criterion_scores: dict[str, int] = Field(
        default_factory=dict,
        description="Rubric criterion code -> 0..100 score. Codes not in the rubric are ignored.",
    )
    revised_hook_suggestion: str | None = Field(default=None, max_length=1000)
    revised_script_suggestion: str | None = Field(default=None, max_length=20000)

    @property
    def passed(self) -> bool:
        """True when the reviewer recommends approving as-is."""
        return self.verdict is ReviewVerdict.APPROVE


class MappingProposal(BaseModel):
    """A proposed ``canonical field -> sheet header`` mapping.

    Produced either deterministically (alias table) or by an LLM. It is a
    *proposal*: a human confirms it before any import happens.
    """

    model_config = ConfigDict(extra="ignore")

    mapping: dict[str, str] = Field(
        default_factory=dict,
        description="Canonical field name -> exact header text from the sheet.",
    )
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    unmapped_headers: list[str] = Field(default_factory=list)
    notes: str = Field(default="", max_length=2000)
