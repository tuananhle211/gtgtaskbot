"""``users`` - people who may talk to MeoBot."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from meobot.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, value_enum
from meobot.domain.access.models import UserStatus
from meobot.domain.identity.models import Role


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A registered team member.

    ``telegram_user_id`` is nullable so a user can be pre-created from the org
    chart before they first message the bot; it is unique so one Telegram
    account maps to exactly one MeoBot user.

    Lifecycle lives here rather than in a second identity table. A suspended
    member is the *same* person as the active one they were yesterday - their
    scripts, approvals and audit rows point at this id, and splitting them
    across two tables would break every one of those relationships.

    ``active`` predates :attr:`status` and is kept in step with it: it is what
    every existing query and the invite flow read, and quietly changing its
    meaning would be a far larger blast radius than keeping both honest.
    """

    __tablename__ = "users"

    telegram_user_id: Mapped[int | None] = mapped_column(
        BigInteger, unique=True, nullable=True, index=True
    )
    telegram_username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[Role] = mapped_column(
        SAEnum(Role, name="role", native_enum=False, length=20, validate_strings=True),
        nullable=False,
        default=Role.EMPLOYEE,
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # --- Lifecycle --------------------------------------------------------
    status: Mapped[UserStatus] = mapped_column(
        value_enum(UserStatus, name="user_status", length=20),
        nullable=False,
        default=UserStatus.ACTIVE,
        index=True,
    )
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    suspended_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: Why the status last changed. Administrative context: it is shown to the
    #: owner and written to the audit trail, and never echoed into a group.
    status_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_status_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    added_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # --- Private-chat reachability ----------------------------------------
    # Telegram will not let a bot open a conversation: the person has to press
    # Start first. So "can we message this member privately" is a fact we have
    # to *learn* and store, not something we can assume from having their id.
    # A username is never used as a destination - it is not a chat.
    telegram_private_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    private_chat_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_private_interaction_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_private_delivery_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    private_delivery_failure_category: Mapped[str | None] = mapped_column(String(40), nullable=True)
    bot_blocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def may_use_meobot(self) -> bool:
        """True only for an account that is both active and not blocked.

        Both flags are consulted on purpose. ``active`` is the older switch the
        invite flow and the identity service already respect; ``status`` is the
        richer one this release introduced. An account has to satisfy both.
        """
        return self.active and self.status is UserStatus.ACTIVE
