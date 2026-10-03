"""User-defined reminders: parsing, recurrence and status vocabulary.

This package was a placeholder until 0.6.0a2. It is now the real thing, and the
shape is deliberate:

* :mod:`~meobot.domain.reminders.parsing` reads a Vietnamese sentence into a
  **draft**, which is not a reminder and is never described as one;
* :mod:`~meobot.domain.reminders.schedule` computes firing instants in the
  user's local timezone and returns UTC;
* :mod:`~meobot.domain.reminders.models` holds the statuses and their
  Vietnamese labels.

Nothing here touches a database, Telegram or a model. Delivery goes through the
same transactional outbox as every other cross-chat message, on
``q_notifications``.
"""

from meobot.domain.reminders.models import (
    KIND_LABELS,
    STATUS_LABELS,
    MissedOccurrencePolicy,
    OccurrenceStatus,
    ReminderDestinationType,
    ReminderStatus,
    ScheduleKind,
    status_label,
    weekday_label,
)
from meobot.domain.reminders.parsing import ParseResult, ReminderDraft, parse_reminder
from meobot.domain.reminders.schedule import ReminderSchedule, describe, describe_instant

__all__ = [
    "KIND_LABELS",
    "STATUS_LABELS",
    "MissedOccurrencePolicy",
    "OccurrenceStatus",
    "ParseResult",
    "ReminderDestinationType",
    "ReminderDraft",
    "ReminderSchedule",
    "ReminderStatus",
    "ScheduleKind",
    "describe",
    "describe_instant",
    "parse_reminder",
    "status_label",
    "weekday_label",
]
