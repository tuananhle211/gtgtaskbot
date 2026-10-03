"""Script Type Registry schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from meobot.db.models.script_type import ScriptType


class ScriptTypeResponse(BaseModel):
    """A script type as exposed by the API."""

    id: uuid.UUID
    code: str
    name: str
    description: str | None
    active: bool
    current_version: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, model: ScriptType) -> ScriptTypeResponse:
        """Map an ORM row onto the wire schema."""
        return cls(
            id=model.id,
            code=model.code,
            name=model.name,
            description=model.description,
            active=model.active,
            current_version=model.current_version,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )


class CreateScriptTypeRequest(BaseModel):
    """Body for ``POST /api/v1/script-types``."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(
        min_length=2,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_]*$",
        description="Stable identifier, e.g. 'doctor_education'.",
    )
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    review_rubric: dict[str, Any] = Field(
        description="Rubric JSON. Criterion weights must sum to exactly 100."
    )
    configuration: dict[str, Any] = Field(default_factory=dict)
    prompt_template: str | None = Field(default=None, max_length=20000)


class ScriptTypeListResponse(BaseModel):
    """Envelope for script type listings."""

    items: list[ScriptTypeResponse]
    total: int
