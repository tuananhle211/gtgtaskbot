"""``confirmation_requests`` - high-risk actions waiting for a human 'yes'."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    String,
    func,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column

from meobot.db.base import Base, JSONColumn, UUIDPrimaryKeyMixin
from meobot.domain.policy.models import ConfirmationState


class ConfirmationRequestRow(Base, UUIDPrimaryKeyMixin):
    """A stored :class:`~meobot.domain.policy.models.ActionPlan` pending approval.

    The suffix ``Row`` avoids a name clash with the domain value object.
    ``telegram_user_id`` is kept next to ``user_id`` so the bootstrap owner -
    who may not yet have a ``users`` row - can still confirm actions.
    """

    __tablename__ = "confirmation_requests"

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    action_plan: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False)
    confirmation_token: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    status: Mapped[ConfirmationState] = mapped_column(
        SAEnum(
            ConfirmationState,
            name="confirmation_state",
            native_enum=False,
            length=20,
            validate_strings=True,
        ),
        nullable=False,
        default=ConfirmationState.PENDING,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
