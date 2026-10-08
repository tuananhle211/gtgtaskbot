"""Profile pictures (0047). One table, ``user_avatars``, at most one row a person.

The browser crops and resizes (256x256, WebP or JPEG); the server only checks
what arrives - :mod:`meobot.application.account.avatar_service` - and keeps the
bytes here. A few tens of kilobytes a person, so the database is the simplest
store: no file system to back up separately, no object storage to configure,
and deleting the account deletes the picture (``ON DELETE CASCADE``).

``version`` grows with every upload and is part of the public URL
(``/api/account/avatar/<user_id>?v=<version>``), which is what lets the image be
cached forever: a new picture is a new URL.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from meobot.db.base import Base
from meobot.db.models.pr import USERS_TABLE

USER_AVATARS = "user_avatars"

#: The decoded size limit, in bytes. The service refuses anything larger; the
#: CHECK is the backstop.
AVATAR_MAX_BYTES = 300 * 1024

#: What may be stored. The browser exports WebP (JPEG where WebP encoding is
#: unsupported); PNG is accepted for completeness.
AVATAR_CONTENT_TYPES = ("image/webp", "image/jpeg", "image/png")


class UserAvatar(Base):
    """One person's current profile picture."""

    __tablename__ = USER_AVATARS
    __table_args__ = (
        CheckConstraint(
            "content_type IN ('image/webp', 'image/jpeg', 'image/png')",
            name="content_type_known",
        ),
        CheckConstraint(f"size_bytes > 0 AND size_bytes <= {AVATAR_MAX_BYTES}", name="size_range"),
        CheckConstraint("version >= 1", name="version_positive"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete="CASCADE"), primary_key=True
    )
    content_type: Mapped[str] = mapped_column(String(20), nullable=False)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Bumped on every upload; the cache-busting ``v`` of the URL.
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


__all__ = ["AVATAR_CONTENT_TYPES", "AVATAR_MAX_BYTES", "USER_AVATARS", "UserAvatar"]
