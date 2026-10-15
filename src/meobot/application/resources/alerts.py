"""The alerts a resource row carries, and which paint it red.

Computed from what the row already loaded (the holder, their seats, the
status' group, the review threshold); nothing is stored, so a person who
leaves today turns every row they hold red today.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from meobot.core.time import ensure_utc
from meobot.db.models.resource import Resource
from meobot.db.models.user import User
from meobot.domain.resources.models import (
    ALERT_LABELS,
    RED_ALERTS,
    AlertKind,
    EmploymentStatus,
    StatusGroup,
)


@dataclass(frozen=True, slots=True)
class Alert:
    kind: AlertKind
    label: str
    #: ``bad`` paints the row red; ``warn`` amber.
    level: str

    @classmethod
    def of(cls, kind: AlertKind) -> Alert:
        return cls(
            kind=kind, label=ALERT_LABELS[kind], level="bad" if kind in RED_ALERTS else "warn"
        )


def alerts_for(
    resource: Resource,
    *,
    holder: User | None,
    holder_seated: bool,
    status_group: StatusGroup | None,
    review_days: int,
    now: datetime,
) -> list[Alert]:
    """Every alert of one row, holder first."""
    found: list[Alert] = []
    if resource.holder_user_id is not None:
        if holder is None or holder.deleted_at is not None or not holder.active:
            found.append(Alert.of(AlertKind.HOLDER_INACTIVE))
        elif holder.employment_status is EmploymentStatus.LEFT:
            found.append(Alert.of(AlertKind.HOLDER_LEFT))
        elif not holder_seated:
            found.append(Alert.of(AlertKind.HOLDER_MOVED))
    if status_group is StatusGroup.LOST:
        found.append(Alert.of(AlertKind.STATUS_LOST))
    elif status_group is StatusGroup.ATTENTION:
        found.append(Alert.of(AlertKind.STATUS_ATTENTION))
    if status_group is not StatusGroup.CLOSED:
        last = resource.reviewed_at or resource.created_at
        if last is not None and ensure_utc(now) - ensure_utc(last) > timedelta(days=review_days):
            found.append(Alert.of(AlertKind.REVIEW_OVERDUE))
    return found


__all__ = ["Alert", "alerts_for"]
