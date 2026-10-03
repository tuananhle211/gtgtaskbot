"""``script_reviews`` - one AI review of one exact script version.

Reviews are never updated. A changed script produces a new version, and a new
version needs a new review; the old review stays attached to the text it
actually judged, together with the rubric snapshot it judged against.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from meobot.db.base import Base, JSONColumn, UUIDPrimaryKeyMixin
from meobot.domain.scripts.models import ReviewVerdict


class ScriptReview(Base, UUIDPrimaryKeyMixin):
    """The stored result of one review run."""

    __tablename__ = "script_reviews"

    script_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scripts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    script_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("script_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    script_type_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("script_type_versions.id", ondelete="SET NULL"), nullable=True
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    rubric_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONColumn, nullable=False, default=dict
    )
    overall_score: Mapped[int] = mapped_column(Integer, nullable=False)
    verdict: Mapped[ReviewVerdict] = mapped_column(
        SAEnum(
            ReviewVerdict,
            name="review_verdict",
            native_enum=False,
            length=30,
            validate_strings=True,
        ),
        nullable=False,
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    strengths: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=list)
    critical_issues: Mapped[dict[str, Any]] = mapped_column(
        JSONColumn, nullable=False, default=list
    )
    recommendations: Mapped[dict[str, Any]] = mapped_column(
        JSONColumn, nullable=False, default=list
    )
    revised_hook_suggestion: Mapped[str | None] = mapped_column(Text, nullable=True)
    revised_script_suggestion: Mapped[str | None] = mapped_column(Text, nullable=True)
    structured_response: Mapped[dict[str, Any]] = mapped_column(
        JSONColumn, nullable=False, default=dict
    )
    usage_metadata: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
