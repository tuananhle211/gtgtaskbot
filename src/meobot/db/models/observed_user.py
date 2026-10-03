"""``observed_telegram_users`` - every Telegram account MeoBot has seen.

Being *seen* is not being *registered*. This table exists so the bot can name a
stranger in an approval message ("Nguyễn Văn A (@nva)") without that stranger
having a role, a permission set, or any presence in ``users``.

The Telegram numeric id is the identity. Usernames and display names are
metadata that people change, and folding a rename into a second row would split
one person's history in half - so the row is keyed by id and the metadata is
overwritten in place.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from meobot.db.base import Base, JSONColumn, TimestampMixin, UUIDPrimaryKeyMixin


class ObservedTelegramUser(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One Telegram account MeoBot has encountered."""

    __tablename__ = "observed_telegram_users"

    telegram_user_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, unique=True, index=True
    )
    latest_username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    latest_display_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    is_bot: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: Room for future non-identifying observations. Never holds message text.
    extra_metadata: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)
