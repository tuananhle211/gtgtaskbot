"""``actor_profiles`` - descriptive context about a person, not their authority.

Kept out of ``users`` deliberately. ``users`` is the authorisation record: a row
there decides what an account may do, and the smallest possible number of code
paths should be able to write to it. This table holds job title, priorities and
free-text notes - things a conversation may update - and nothing in it is ever
consulted by the policy engine.

``role`` is therefore *absent* here. It is read from ``users`` every time, so a
profile can never grant a permission.
"""

from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from meobot.db.base import Base, JSONColumn, TimestampMixin, UUIDPrimaryKeyMixin


class ActorProfileRow(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Conversational context for one Telegram account.

    Keyed by ``telegram_user_id`` rather than by ``users.id`` so the bootstrap
    owner - who has no ``users`` row until they are promoted - still gets a
    profile. ``user_id`` is filled in opportunistically when a row does exist.
    """

    __tablename__ = "actor_profiles"

    telegram_user_id: Mapped[int] = mapped_column(
        BigInteger, unique=True, nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: Initialised from Telegram on first contact; manual edits win afterwards,
    #: because a person's Telegram display name is not necessarily their name.
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    preferred_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    preferred_address: Mapped[str | None] = mapped_column(String(40), nullable=True)
    job_title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    organization: Mapped[str | None] = mapped_column(String(200), nullable=True)
    department: Mapped[str | None] = mapped_column(String(200), nullable=True)
    team: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # List-valued columns carry an explicit JSON type: the declarative
    # annotation map only knows how to render ``dict[str, Any]``.
    responsibilities: Mapped[list[str]] = mapped_column(JSONColumn, nullable=False, default=list)
    communication_preferences: Mapped[list[str]] = mapped_column(
        JSONColumn, nullable=False, default=list
    )
    content_domains: Mapped[list[str]] = mapped_column(JSONColumn, nullable=False, default=list)
    current_priorities: Mapped[list[str]] = mapped_column(JSONColumn, nullable=False, default=list)
    profile_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
