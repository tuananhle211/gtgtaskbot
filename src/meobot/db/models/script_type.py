"""``script_types`` and ``script_type_versions`` - the Script Type Registry."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from meobot.db.base import Base, JSONColumn, TimestampMixin, UUIDPrimaryKeyMixin


class ScriptType(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A content genre (``doctor_education``, ``short_drama``, ...).

    ``current_version`` points at the version used for new reviews. Older
    versions stay readable so past reviews remain explainable.
    """

    __tablename__ = "script_types"

    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    versions: Mapped[list[ScriptTypeVersion]] = relationship(
        back_populates="script_type",
        cascade="all, delete-orphan",
        order_by="ScriptTypeVersion.version",
        lazy="selectin",
    )


class ScriptTypeVersion(Base, UUIDPrimaryKeyMixin):
    """An immutable snapshot of a script type's configuration and rubric."""

    __tablename__ = "script_type_versions"
    __table_args__ = (
        UniqueConstraint("script_type_id", "version", name="uq_script_type_versions_type_version"),
    )

    script_type_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("script_types.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)
    review_rubric: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False)
    prompt_template: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    script_type: Mapped[ScriptType] = relationship(back_populates="versions")
