"""``scripts`` and ``script_versions`` - imported scripts and their history.

Versioning rule: a row read from Google Sheets becomes a new
:class:`ScriptVersion` only when its *content* hash changes. Versions are
append-only, which is what makes "this review belongs to exactly that text"
and "this approval approved exactly that text" true statements rather than
hopeful ones.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from meobot.db.base import Base, JSONColumn, TimestampMixin, UUIDPrimaryKeyMixin
from meobot.domain.scripts.workflow import ScriptStatus


class Script(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One script tracked by MeoBot, sourced from a sheet row.

    ``external_script_id`` is the sheet's own identifier (or a synthesised
    row-based one). It is unique per profile so re-reading the same sheet
    updates the same script instead of importing duplicates.
    """

    __tablename__ = "scripts"
    __table_args__ = (
        UniqueConstraint(
            "sheet_profile_id",
            "external_script_id",
            name="uq_scripts_profile_external_id",
        ),
        Index("ix_scripts_status_updated_at", "status", "updated_at"),
    )

    sheet_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sheet_profiles.id", ondelete="SET NULL"), nullable=True, index=True
    )
    external_script_id: Mapped[str] = mapped_column(String(200), nullable=False)
    source_row_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "script_versions.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_scripts_current_version_id_script_versions",
        ),
        nullable=True,
    )
    script_type_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("script_types.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[ScriptStatus] = mapped_column(
        SAEnum(
            ScriptStatus,
            name="script_status",
            native_enum=False,
            length=40,
            validate_strings=True,
        ),
        nullable=False,
        default=ScriptStatus.IMPORTED,
        index=True,
    )
    author: Mapped[str | None] = mapped_column(String(200), nullable=True)
    deadline: Mapped[date | None] = mapped_column(Date, nullable=True)
    channel: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    current_version: Mapped[ScriptVersion | None] = relationship(
        foreign_keys=[current_version_id],
        lazy="selectin",
        post_update=True,
    )
    versions: Mapped[list[ScriptVersion]] = relationship(
        back_populates="script",
        cascade="all, delete-orphan",
        order_by="ScriptVersion.version_number",
        foreign_keys="ScriptVersion.script_id",
        lazy="selectin",
    )


class ScriptVersion(Base, UUIDPrimaryKeyMixin):
    """An immutable snapshot of a script's content at one point in time."""

    __tablename__ = "script_versions"
    __table_args__ = (
        UniqueConstraint("script_id", "version_number", name="uq_script_versions_script_version"),
        Index("ix_script_versions_source_hash", "script_id", "source_hash"),
    )

    script_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scripts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    hook: Mapped[str | None] = mapped_column(Text, nullable=True)
    script_body: Mapped[str] = mapped_column(Text, nullable=False)
    production_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_row_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw_source_data: Mapped[dict[str, Any]] = mapped_column(
        JSONColumn, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    script: Mapped[Script] = relationship(back_populates="versions", foreign_keys=[script_id])
