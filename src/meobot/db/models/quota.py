"""Daily AI-chat usage ledger and member quota requests.

One row per member per *local* calendar day. Keying by the day rather than
filtering timestamps is what makes the midnight reset free: at 00:00 in
``Asia/Ho_Chi_Minh`` the key changes, the next lookup finds nothing, and a fresh
row starts at zero. No scheduled task can be late, because none is involved.

``reserved_count`` is the concurrency mechanism. A slot is reserved before the
provider is called and converted into a use only once Telegram has accepted the
reply, so a failed generation or a failed send costs the member nothing while
two simultaneous messages still cannot both take the last slot.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from meobot.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, value_enum
from meobot.domain.access.models import PendingRequestStatus, QuotaAction
from meobot.domain.access.quota import MEMBER_DEFAULT_DAILY_LIMIT


class DailyAiUsage(Base, UUIDPrimaryKeyMixin):
    """One member's chat allowance and consumption for one local day."""

    __tablename__ = "daily_ai_usage"
    __table_args__ = (
        UniqueConstraint("user_id", "quota_date", name="uq_daily_ai_usage_user_id_quota_date"),
        Index("ix_daily_ai_usage_quota_date", "quota_date"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: The *local* calendar day, already converted. Never a UTC date.
    quota_date: Mapped[date] = mapped_column(Date, nullable=False)
    base_limit: Mapped[int] = mapped_column(
        Integer, nullable=False, default=MEMBER_DEFAULT_DAILY_LIMIT
    )
    #: Granted for this day only.
    temporary_bonus: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: A standing replacement for ``base_limit``; ``NULL`` means "use the base".
    persistent_override: Mapped[int | None] = mapped_column(Integer, nullable=True)
    used_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: In-flight answers, released on failure and converted on delivery.
    reserved_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Lets a restart tell a genuinely in-flight reservation from a stranded
    #: one left behind by a process that died mid-turn.
    oldest_reservation_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UserQuotaOverride(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A standing daily limit for one member, applied to every future day.

    Separate from :class:`DailyAiUsage` because that table is per-day: writing
    the standing limit there would only change today, and tomorrow would revert
    to the default. This row is copied onto each new day's ledger as it opens.
    """

    __tablename__ = "user_quota_overrides"
    __table_args__ = (UniqueConstraint("user_id", name="uq_user_quota_overrides_user_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    daily_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_by_telegram_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class QuotaRequest(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A member asking the owner for more chat quota today.

    At most one open row per member per quota date, so pressing "Xin thêm lượt"
    repeatedly produces one notification rather than a stream of them.
    """

    __tablename__ = "quota_requests"
    __table_args__ = (
        UniqueConstraint("user_id", "quota_date", name="uq_quota_requests_user_id_quota_date"),
        Index("ix_quota_requests_status", "status"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    requester_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    requester_display_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    quota_date: Mapped[date] = mapped_column(Date, nullable=False)
    limit_at_request: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[PendingRequestStatus] = mapped_column(
        value_enum(PendingRequestStatus, name="pending_request_status", length=20),
        nullable=False,
        default=PendingRequestStatus.OPEN,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_action: Mapped[QuotaAction | None] = mapped_column(
        value_enum(QuotaAction, name="quota_action", length=10),
        nullable=True,
    )
    resolved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
