"""``invite_codes`` - how an employee joins MeoBot.

Only the hash of a code is stored. If this table leaks, nobody gains an
account: the plaintext exists exactly once, in the Telegram message that
delivered it to the creator.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from meobot.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from meobot.domain.identity.models import Role
from meobot.domain.units.models import UnitMemberRole


class InviteCode(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A single-use (or limited-use) code granting a role on redemption."""

    __tablename__ = "invite_codes"

    code_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    role: Mapped[Role] = mapped_column(
        SAEnum(Role, name="role", native_enum=False, length=20, validate_strings=True),
        nullable=False,
        default=Role.EMPLOYEE,
    )
    scope: Mapped[str | None] = mapped_column(
        String(100), nullable=True, doc="Optional team/department label carried onto the user."
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_by_telegram_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    max_uses: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    use_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: 0051: where the redeemer lands - a stream lead's code tags them into
    #: that stream with this role, reporting to ``manager_user_id``. All null
    #: for an OWNER/ADMIN code: the redeemer joins untagged.
    unit_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("org_units.id", ondelete="RESTRICT"), nullable=True
    )
    unit_role: Mapped[UnitMemberRole | None] = mapped_column(
        SAEnum(
            UnitMemberRole,
            name="invite_unit_role",
            native_enum=False,
            length=20,
            validate_strings=True,
        ),
        nullable=True,
    )
    manager_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
