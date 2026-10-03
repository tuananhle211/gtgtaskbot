"""``conversation_states`` - just enough memory to finish a multi-step flow.

This is not an agent memory. It holds the structured state of one in-progress
workflow (adding a sheet, correcting a mapping, typing a revision comment) and
nothing else, it expires, and a nightly task deletes what was abandoned.

The table backs :class:`meobot.bot.storage.PostgresStorage`, so an aiogram FSM
survives a bot restart.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from meobot.db.base import Base, JSONColumn, TimestampMixin, UUIDPrimaryKeyMixin


class ConversationState(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One conversation's FSM state and payload."""

    __tablename__ = "conversation_states"
    __table_args__ = (
        UniqueConstraint(
            "bot_id",
            "chat_id",
            "telegram_user_id",
            "destiny",
            name="uq_conversation_states_key",
        ),
    )

    bot_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    destiny: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    state: Mapped[str | None] = mapped_column(String(200), nullable=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
