"""``audit_logs`` - append-only record of everything that mattered."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column

from meobot.db.base import Base, JSONColumn, UUIDPrimaryKeyMixin
from meobot.domain.audit.models import AuditResult


class AuditLog(Base, UUIDPrimaryKeyMixin):
    """One audited action.

    Rows are never updated or deleted. ``actor_telegram_id`` is duplicated
    alongside ``actor_user_id`` so the trail survives a user row being removed
    and so bootstrap-owner actions (no ``users`` row yet) are still attributable.
    """

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_action_created_at", "action", "created_at"),
        Index("ix_audit_logs_entity", "entity_type", "entity_id"),
    )

    request_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    actor_telegram_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    before_data: Mapped[dict[str, Any] | None] = mapped_column(JSONColumn, nullable=True)
    after_data: Mapped[dict[str, Any] | None] = mapped_column(JSONColumn, nullable=True)
    result: Mapped[AuditResult] = mapped_column(
        SAEnum(
            AuditResult,
            name="audit_result",
            native_enum=False,
            length=30,
            validate_strings=True,
        ),
        nullable=False,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
