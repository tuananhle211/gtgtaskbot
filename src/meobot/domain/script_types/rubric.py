"""Review rubric model and its invariants.

A rubric is stored as JSON on ``script_type_versions.review_rubric``. Rubrics
are immutable once persisted: changing one creates a new version, so historical
AI reviews stay explainable.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic import ValidationError as PydanticValidationError

from meobot.core.errors import ValidationError

#: Rubric weights must sum to exactly this value.
TOTAL_WEIGHT = 100


class RubricCriterion(BaseModel):
    """One scored dimension of a script review."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str = Field(min_length=2, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    name: str = Field(min_length=1, max_length=200)
    weight: int = Field(ge=0, le=TOTAL_WEIGHT, description="Percentage points of the total score")
    description: str | None = Field(default=None, max_length=1000)
    guidance: str | None = Field(
        default=None,
        max_length=2000,
        description="Instruction handed to the LLM reviewer for this criterion.",
    )


class ReviewRubric(BaseModel):
    """A complete, self-consistent rubric."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    criteria: list[RubricCriterion] = Field(min_length=1)
    passing_score: int = Field(default=70, ge=0, le=TOTAL_WEIGHT)
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("criteria")
    @classmethod
    def _unique_codes(cls, criteria: list[RubricCriterion]) -> list[RubricCriterion]:
        seen: set[str] = set()
        duplicates: set[str] = set()
        for criterion in criteria:
            if criterion.code in seen:
                duplicates.add(criterion.code)
            seen.add(criterion.code)
        if duplicates:
            raise ValueError(f"Duplicate criterion codes: {sorted(duplicates)}")
        return criteria

    @model_validator(mode="after")
    def _weights_sum_to_total(self) -> ReviewRubric:
        total = sum(criterion.weight for criterion in self.criteria)
        if total != TOTAL_WEIGHT:
            raise ValueError(f"Rubric weights must sum to {TOTAL_WEIGHT}, got {total}")
        return self

    @property
    def total_weight(self) -> int:
        return sum(criterion.weight for criterion in self.criteria)

    def criterion(self, code: str) -> RubricCriterion:
        """Look up a criterion by code."""
        for criterion in self.criteria:
            if criterion.code == code:
                return criterion
        raise ValidationError(f"Unknown criterion code: {code!r}")


class ScriptTypeConfiguration(BaseModel):
    """Non-rubric settings that also belong to a script type version."""

    model_config = ConfigDict(extra="allow")

    target_duration_seconds: int | None = Field(default=None, ge=5, le=3600)
    tone: str | None = None
    required_fields: list[str] = Field(default_factory=list)
    channel_hints: list[str] = Field(default_factory=list)


def validate_rubric_payload(payload: dict[str, Any]) -> ReviewRubric:
    """Parse and validate raw JSON into a :class:`ReviewRubric`.

    Raises:
        ValidationError: when the payload violates any rubric invariant
            (weights not summing to 100, duplicate codes, negative weights).
    """
    try:
        return ReviewRubric.model_validate(payload)
    except PydanticValidationError as exc:
        messages = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        raise ValidationError(f"Invalid review rubric: {messages}") from exc
