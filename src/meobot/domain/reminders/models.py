"""Vocabulary for user-defined reminders.

A reminder is the first thing in MeoBot a person creates *for the future*, which
makes one rule load-bearing: **nothing here is said to exist until a row is
committed.** The wording MeoBot uses is derived from these statuses, and the
preview stage has no status at all - a draft is not a reminder, and is never
described as one.

Internal statuses stay English, as everywhere else in this codebase. Every one
of them has a Vietnamese label below, and only the label is ever shown.
"""

from __future__ import annotations

from enum import StrEnum


class ScheduleKind(StrEnum):
    """How often a reminder fires.

    Monthly is deliberately absent rather than half-built: see
    :data:`UNSUPPORTED_MONTHLY` for what MeoBot says when somebody asks for it.
    """

    ONE_TIME = "ONE_TIME"
    DAILY = "DAILY"
    WEEKLY = "WEEKLY"


class ReminderStatus(StrEnum):
    """Lifecycle of one reminder. Nothing is ever hard-deleted."""

    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"

    @property
    def is_live(self) -> bool:
        """True when the sweep should still consider this reminder."""
        return self is ReminderStatus.ACTIVE


class ReminderDestinationType(StrEnum):
    """Where a reminder is delivered."""

    USER_PRIVATE = "USER_PRIVATE"
    REGISTERED_CHAT = "REGISTERED_CHAT"


class OccurrenceStatus(StrEnum):
    """What happened to one scheduled firing.

    ``SKIPPED`` exists so a missed occurrence leaves a record. A reminder that
    silently did not fire is indistinguishable from one that was never created,
    and that is exactly the confusion this release was opened to end.
    """

    SCHEDULED = "SCHEDULED"
    QUEUED = "QUEUED"
    DELIVERED = "DELIVERED"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"


class MissedOccurrencePolicy(StrEnum):
    """What to do about firings that were due while nothing was running.

    ``DELIVER_LATEST`` is the default and the safe one: after a two-day
    outage, a daily reminder should produce one message, not two. Delivering
    every missed occurrence turns a restart into a flood, and the older ones
    are advice about a moment that has passed.
    """

    DELIVER_LATEST = "DELIVER_LATEST"
    SKIP_ALL = "SKIP_ALL"


STATUS_LABELS: dict[ReminderStatus, str] = {
    ReminderStatus.ACTIVE: "Hoạt động",
    ReminderStatus.PAUSED: "Tạm dừng",
    ReminderStatus.CANCELLED: "Đã huỷ",
    ReminderStatus.COMPLETED: "Đã hoàn thành",
}

KIND_LABELS: dict[ScheduleKind, str] = {
    ScheduleKind.ONE_TIME: "Một lần",
    ScheduleKind.DAILY: "Hằng ngày",
    ScheduleKind.WEEKLY: "Hằng tuần",
}

#: Monday-first, matching :meth:`datetime.date.weekday`.
WEEKDAY_LABELS: tuple[str, ...] = (
    "thứ Hai",
    "thứ Ba",
    "thứ Tư",
    "thứ Năm",
    "thứ Sáu",
    "thứ Bảy",
    "Chủ nhật",
)

#: Said verbatim when somebody asks for a monthly reminder. Truthful rather
#: than approximate: pretending a monthly rule works by scheduling something
#: weekly would be worse than declining.
UNSUPPORTED_MONTHLY = (
    "MeoBot chưa làm được lịch nhắc theo tháng. Hiện MeoBot làm được lịch nhắc "
    "một lần, hằng ngày và hằng tuần."
)


def status_label(status: ReminderStatus) -> str:
    """Vietnamese name of a reminder status. A raw value never reaches a user."""
    return STATUS_LABELS[status]


def weekday_label(weekday: int) -> str:
    """Vietnamese name of a weekday, Monday being 0."""
    return WEEKDAY_LABELS[weekday % 7]
