"""``script_approvals`` - who decided what, about which exact version.

An approval names a :class:`~meobot.db.models.script.ScriptVersion`, not a
script. When the sheet text changes afterwards, the row stays as historical
evidence but no longer approves anything: the new version starts unapproved.

``action`` only ever holds :class:`~meobot.domain.scripts.models.ApprovalAction`
values - there is no publish-approval action here by design (ADR-002).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Text, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from meobot.db.base import Base, UUIDPrimaryKeyMixin
from meobot.domain.scripts.models import ApprovalAction
from meobot.domain.scripts.workflow import ScriptStatus


class ScriptApproval(Base, UUIDPrimaryKeyMixin):
    """One human decision on one script version."""

    __tablename__ = "script_approvals"

    script_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scripts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    script_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("script_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    action: Mapped[ApprovalAction] = mapped_column(
        SAEnum(
            ApprovalAction,
            name="approval_action",
            native_enum=False,
            length=40,
            validate_strings=True,
        ),
        nullable=False,
    )
    status_before: Mapped[ScriptStatus] = mapped_column(
        SAEnum(
            ScriptStatus,
            name="script_status",
            native_enum=False,
            length=40,
            validate_strings=True,
        ),
        nullable=False,
    )
    status_after: Mapped[ScriptStatus] = mapped_column(
        SAEnum(
            ScriptStatus,
            name="script_status",
            native_enum=False,
            length=40,
            validate_strings=True,
        ),
        nullable=False,
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_telegram_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
