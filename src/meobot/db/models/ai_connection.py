"""AI connections ("Kết nối AI", 0064) and which serve which purpose.

See :mod:`meobot.domain.ai.models`. The API key is stored encrypted
(:mod:`meobot.core.secrets`, AAD ``ai_connection:<id>``); only its last four
characters are kept in clear, for the screen.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from meobot.db.base import Base, JSONColumn, TimestampMixin, UUIDPrimaryKeyMixin, value_enum
from meobot.db.models.pr import RESTRICT, USERS_TABLE
from meobot.domain.ai.models import AiProviderKind, AiPurpose

AI_CONNECTIONS = "ai_connections"
AI_ROUTES = "ai_routes"


class AiConnection(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = AI_CONNECTIONS
    __table_args__ = (
        CheckConstraint("length(trim(name)) > 0", name="name_not_empty"),
        CheckConstraint("length(trim(model)) > 0", name="model_not_empty"),
    )

    name: Mapped[str] = mapped_column(String(60), nullable=False)
    kind: Mapped[AiProviderKind] = mapped_column(
        value_enum(AiProviderKind, name="ai_provider_kind", length=20), nullable=False
    )
    #: None = the kind's own endpoint.
    base_url: Mapped[str | None] = mapped_column(String(300), nullable=True)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    #: ``v1:<key id>:<nonce>:<ciphertext>`` - never the key itself.
    api_key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    #: ``••••abcd``: what the screen shows of the key.
    api_key_hint: Mapped[str] = mapped_column(String(12), nullable=False, default="")
    #: Off = never called, whatever the routes say.
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    #: The last "Kiểm tra kết nối".
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_test_message: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )


class AiRoute(Base, TimestampMixin):
    """One purpose's chain: the first connection, then its fallbacks."""

    __tablename__ = AI_ROUTES

    purpose: Mapped[AiPurpose] = mapped_column(
        value_enum(AiPurpose, name="ai_purpose", length=20), primary_key=True
    )
    #: Connection ids (as strings), in the order they are tried.
    connection_ids: Mapped[list[Any]] = mapped_column(JSONColumn, nullable=False, default=list)


__all__ = ["AI_CONNECTIONS", "AI_ROUTES", "AiConnection", "AiRoute"]
