"""Leave and late-arrival requests, their history, and the work calendar.

``hr_requests`` carries a ``version`` column, and every mutation bumps it. That
is what makes an approval button safe: the button is signed against the version
it was rendered for, so approving a request that has been amended since fails
instead of approving something the owner never read.

``hr_request_events`` is append-only. A request is never hard-deleted and its
history is never rewritten - withdrawing, amending and cancelling all add a row
rather than removing one, because "who asked for what, and who decided" is the
question this table exists to answer.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from meobot.db.base import Base, JSONColumn, TimestampMixin, UUIDPrimaryKeyMixin, value_enum
from meobot.domain.hr.models import HrEventType, HrRequestStatus, HrRequestType


class HrRequest(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One request to be away, or to arrive late."""

    __tablename__ = "hr_requests"
    __table_args__ = (
        Index("ix_hr_requests_requester_status", "requester_user_id", "status"),
        Index("ix_hr_requests_work_date_status", "work_date", "status"),
    )

    requester_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    request_type: Mapped[HrRequestType] = mapped_column(
        value_enum(HrRequestType, name="hr_request_type", length=30), nullable=False
    )
    status: Mapped[HrRequestStatus] = mapped_column(
        value_enum(HrRequestStatus, name="hr_request_status", length=20),
        nullable=False,
        default=HrRequestStatus.DRAFT,
        index=True,
    )

    #: Stored in UTC, like every timestamp here. The local wall-clock reading is
    #: produced at render time from the active work schedule's timezone.
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: The *local* calendar day the request is about. Kept separately from
    #: ``start_at`` so "who is off today" is one indexed equality test rather
    #: than a timezone-aware range scan.
    work_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    expected_arrival_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    late_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Approver-only note. Never rendered to the requester and never posted in a
    #: group - see the privacy tests.
    private_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: Bumped on every mutation; bound into approval buttons.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    #: Set once the approver has been told, so a reminder sweep cannot notify
    #: the same request twice.
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class HrRequestEvent(Base, UUIDPrimaryKeyMixin):
    """One append-only line of a request's history."""

    __tablename__ = "hr_request_events"
    __table_args__ = (Index("ix_hr_request_events_request_created", "request_id", "created_at"),)

    request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hr_requests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[HrEventType] = mapped_column(
        value_enum(HrEventType, name="hr_event_type", length=30), nullable=False
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_telegram_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    state_before: Mapped[str | None] = mapped_column(String(30), nullable=True)
    state_after: Mapped[str | None] = mapped_column(String(30), nullable=True)
    #: Structured context. Never the private reason, never a secret.
    event_metadata: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )


class WorkSchedule(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """When the organisation works.

    At most one row is active at a time. Its absence is meaningful: MeoBot
    refuses to compute lateness rather than assuming an office opens at 08:00,
    and tells the owner to configure it.
    """

    __tablename__ = "work_schedules"

    name: Mapped[str] = mapped_column(String(100), nullable=False, default="Mặc định")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Ho_Chi_Minh")
    #: Monday=0 … Sunday=6.
    working_days: Mapped[list[int]] = mapped_column(JSONColumn, nullable=False, default=list)
    morning_start: Mapped[time] = mapped_column(Time, nullable=False)
    morning_end: Mapped[time] = mapped_column(Time, nullable=False)
    afternoon_start: Mapped[time] = mapped_column(Time, nullable=False)
    afternoon_end: Mapped[time] = mapped_column(Time, nullable=False)
    active_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    active_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class OrganizationHoliday(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A company non-working day."""

    __tablename__ = "organization_holidays"
    __table_args__ = (UniqueConstraint("holiday_date", name="uq_organization_holidays_date"),)

    holiday_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    is_paid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class MemberListContext(Base, UUIDPrimaryKeyMixin):
    """The numbered list a Member last saw, so "việc số 2" means something.

    Bound to (bot, chat, person) and carrying a version and an expiry, because
    the failure this prevents is specific: somebody says "việc số 2" about a
    list from ten minutes ago while a newer list is on screen. Binding the
    numbering to a *version* means a stale reference resolves to nothing and
    MeoBot asks again, rather than acting on the wrong item.
    """

    __tablename__ = "member_list_contexts"
    __table_args__ = (
        UniqueConstraint(
            "bot_id", "chat_id", "telegram_user_id", "kind", name="uq_member_list_contexts_key"
        ),
    )

    bot_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    #: Which list this is - "hr_request", "work", … - so two lists can coexist.
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    #: Ordered entity ids, position 1 being what the person read as "1".
    item_ids: Mapped[list[str]] = mapped_column(JSONColumn, nullable=False, default=list)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
