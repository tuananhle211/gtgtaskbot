"""``user_permissions`` (0066): one person's extra right in one module.

The **personal layer** of authorization, on top of what the person's
department seats grant (see :mod:`meobot.domain.org.modules`). A row says
*this person holds this permission of this module with this scope*, who gave
it, until when, and - once withdrawn - when and by whom. Rows are never
deleted: a decision taken under a grant that existed then must stay
explicable.

The PR module's three review gates are **not** here: they carry a content-type
and channel filter and stay in ``pr_user_capabilities``.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from meobot.core.time import utcnow
from meobot.db.base import Base, UUIDPrimaryKeyMixin
from meobot.db.models.pr import RESTRICT, USERS_TABLE

USER_PERMISSIONS = "user_permissions"


class UserPermission(Base, UUIDPrimaryKeyMixin):
    __tablename__ = USER_PERMISSIONS
    __table_args__ = (
        CheckConstraint("scope IN ('OWN', 'ALL')", name="scope_known"),
        Index("ix_user_permissions_user_id", "user_id"),
        Index("ix_user_permissions_module_permission", "module", "permission"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=False
    )
    module: Mapped[str] = mapped_column(String(20), nullable=False)
    permission: Mapped[str] = mapped_column(String(40), nullable=False)
    #: ``OWN`` / ``ALL``.
    scope: Mapped[str] = mapped_column(String(10), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    granted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )
    #: Last day in force; ``NULL`` = no planned end.
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )


__all__ = ["USER_PERMISSIONS", "UserPermission"]
