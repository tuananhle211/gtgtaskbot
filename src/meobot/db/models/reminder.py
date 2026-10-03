"""Durable reminders, and one row per firing.

Two tables rather than one, and the split is what makes the scheduler safe.

``reminders`` is the *rule*: "16:00 every Thursday, tell me to teach". It has a
``next_run_at``, which is the only thing the sweep query looks at.

``reminder_occurrences`` is one *firing*. ``(reminder_id, scheduled_for)`` is
unique, and that constraint is the entire defence against duplicate reminders.
Two Beat processes, an overlapping sweep, a retried task and a worker restart
all try to insert the same ``(rule, instant)`` pair; exactly one wins. Without
it, "run this every 60 seconds" would eventually mean "send this twice".

The occurrence also carries the outbox row it produced, so "was this reminder
actually delivered" is answerable by joining two tables rather than by reading
logs.
"""

from __future__ import annotations

import uuid
from datetime import datetime, time

from sqlalchemy import (
    BigInteger,
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

from meobot.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, value_enum
from meobot.domain.reminders.models import (
    MissedOccurrencePolicy,
    OccurrenceStatus,
    ReminderDestinationType,
    ReminderStatus,
    ScheduleKind,
)


class Reminder(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One standing instruction to say something at a time."""

    __tablename__ = "reminders"
    __table_args__ = (
        # The sweep's query: live reminders whose moment has come.
        Index("ix_reminders_due", "status", "next_run_at"),
        Index("ix_reminders_owner_status", "owner_user_id", "status"),
    )

    #: Whose reminder this is. Also who may pause, edit or cancel it.
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: Usually the same person. Different when a manager sets one for a group.
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: Where it was created. Audited alongside the destination.
    source_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    destination_type: Mapped[ReminderDestinationType] = mapped_column(
        value_enum(ReminderDestinationType, name="reminder_destination_type", length=30),
        nullable=False,
        default=ReminderDestinationType.USER_PRIVATE,
    )
    destination_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    destination_chat_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("telegram_chats.id", ondelete="SET NULL"), nullable=True
    )

    content: Mapped[str] = mapped_column(Text, nullable=False)
    #: The zone the wall clock refers to. Stored per reminder rather than read
    #: from configuration at fire time, so changing the deployment's timezone
    #: cannot silently move every existing reminder.
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Ho_Chi_Minh")
    schedule_kind: Mapped[ScheduleKind] = mapped_column(
        value_enum(ScheduleKind, name="reminder_schedule_kind", length=20), nullable=False
    )
    #: Compact, and only ever one of the three shapes this version can run.
    recurrence_rule: Mapped[str] = mapped_column(String(60), nullable=False)
    #: The wall clock, kept separately so a list can show it without parsing.
    local_time: Mapped[time] = mapped_column(Time, nullable=False)

    #: UTC. The only column the sweep filters on.
    next_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    status: Mapped[ReminderStatus] = mapped_column(
        value_enum(ReminderStatus, name="reminder_status", length=20),
        nullable=False,
        default=ReminderStatus.ACTIVE,
        index=True,
    )
    missed_occurrence_policy: Mapped[MissedOccurrencePolicy] = mapped_column(
        value_enum(MissedOccurrencePolicy, name="missed_occurrence_policy", length=30),
        nullable=False,
        default=MissedOccurrencePolicy.DELIVER_LATEST,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ReminderOccurrence(Base, UUIDPrimaryKeyMixin):
    """One firing of one reminder.

    ``(reminder_id, scheduled_for)`` is unique. That is the whole duplicate
    defence - see the module docstring.
    """

    __tablename__ = "reminder_occurrences"
    __table_args__ = (
        UniqueConstraint(
            "reminder_id", "scheduled_for", name="uq_reminder_occurrences_reminder_moment"
        ),
        Index("ix_reminder_occurrences_status", "status"),
    )

    reminder_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("reminders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: UTC instant this firing was *for*, not when it was created.
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    outbox_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("outbound_messages.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[OccurrenceStatus] = mapped_column(
        value_enum(OccurrenceStatus, name="occurrence_status", length=20),
        nullable=False,
        default=OccurrenceStatus.SCHEDULED,
    )
    #: Why an occurrence was skipped, in the application's own words. Set when
    #: a firing is older than the grace window - a skipped reminder leaves a
    #: record rather than vanishing.
    skip_reason: Mapped[str | None] = mapped_column(String(60), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
