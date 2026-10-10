"""Deadlines and token days for ORD (0053). Pure: no database, no clock.

A node's deadline is set by its Leader; the order's ``desired_deadline_at``
is the orderer's wish. :func:`deadline_status` names where a deadline stands
for the board and the task page; :func:`work_day` is the Vietnamese calendar
day a token ledger row lands on.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from zoneinfo import ZoneInfo

from meobot.domain.orders.models import ACTIVE_NODE_STATUSES, OrderNodeStatus

#: The day a token is taken on, and the calendar the effort grid shows.
WORK_TIMEZONE = ZoneInfo("Asia/Ho_Chi_Minh")
#: Less than this left on an active deadline is "Sắp tới hạn".
DUE_SOON = timedelta(hours=24)
#: The largest token value a node takes (``Numeric(6, 2)``).
MAX_TOKENS = Decimal("999.99")


class DeadlineStatus(StrEnum):
    ON_TRACK = "ON_TRACK"
    DUE_SOON = "DUE_SOON"
    OVERDUE = "OVERDUE"
    MET = "MET"
    MISSED = "MISSED"


DEADLINE_LABELS: dict[DeadlineStatus, str] = {
    DeadlineStatus.ON_TRACK: "Còn hạn",
    DeadlineStatus.DUE_SOON: "Sắp tới hạn",
    DeadlineStatus.OVERDUE: "Quá hạn",
    DeadlineStatus.MET: "Đúng hạn",
    DeadlineStatus.MISSED: "Trễ hạn",
}


def open_status(deadline: datetime | None, now: datetime) -> DeadlineStatus | None:
    """An unfinished thing's standing against ``deadline``."""
    if deadline is None:
        return None
    if now > deadline:
        return DeadlineStatus.OVERDUE
    if deadline - now < DUE_SOON:
        return DeadlineStatus.DUE_SOON
    return DeadlineStatus.ON_TRACK


def done_status(met: bool | None) -> DeadlineStatus | None:
    if met is None:
        return None
    return DeadlineStatus.MET if met else DeadlineStatus.MISSED


def node_deadline(
    *,
    deadline_at: datetime | None,
    revision_deadline_at: datetime | None,
    revision_count: int,
) -> datetime | None:
    """The deadline that counts now: a revision round's own, when the node was
    sent back and its Leader set one, else the node's deadline."""
    if revision_count > 0 and revision_deadline_at is not None:
        return revision_deadline_at
    return deadline_at


def node_deadline_status(
    *,
    status: OrderNodeStatus,
    deadline_at: datetime | None,
    revision_deadline_at: datetime | None,
    revision_count: int,
    deadline_met: bool | None,
    now: datetime,
) -> DeadlineStatus | None:
    """Active: against the deadline that counts now. Done: the first
    completion's verdict. Not reached / skipped: nothing."""
    if status in ACTIVE_NODE_STATUSES:
        return open_status(
            node_deadline(
                deadline_at=deadline_at,
                revision_deadline_at=revision_deadline_at,
                revision_count=revision_count,
            ),
            now,
        )
    if status is OrderNodeStatus.HOAN_THANH:
        return done_status(deadline_met)
    return None


def work_day(at: datetime) -> date:
    """The Vietnamese calendar day of the instant ``at``."""
    return at.astimezone(WORK_TIMEZONE).date()


def parse_tokens(value: Decimal | float | int | str | None) -> Decimal | None:
    """Tokens as typed: 0 ≤ x ≤ 999.99, two decimals. None stays None;
    anything else raises ``ValueError``."""
    if value is None:
        return None
    try:
        tokens = Decimal(str(value)).quantize(Decimal("0.01"))
    except InvalidOperation as error:
        raise ValueError(value) from error
    if tokens < 0 or tokens > MAX_TOKENS:
        raise ValueError(value)
    return tokens


def overrun(deadline: datetime | None, desired: datetime | None) -> timedelta | None:
    """How far ``deadline`` lands past the orderer's wish; None when it does not."""
    if deadline is None or desired is None or deadline <= desired:
        return None
    return deadline - desired


def describe_overrun(gap: timedelta) -> str:
    """``"1 ngày 3 giờ"``, ``"5 giờ"``, ``"40 phút"``."""
    minutes = int(gap.total_seconds() // 60)
    days, minutes = divmod(minutes, 24 * 60)
    hours, minutes = divmod(minutes, 60)
    parts = []
    if days:
        parts.append(f"{days} ngày")
    if hours:
        parts.append(f"{hours} giờ")
    if not days and not hours:
        parts.append(f"{max(minutes, 1)} phút")
    return " ".join(parts)


__all__ = [
    "DEADLINE_LABELS",
    "DUE_SOON",
    "MAX_TOKENS",
    "WORK_TIMEZONE",
    "DeadlineStatus",
    "describe_overrun",
    "done_status",
    "node_deadline",
    "node_deadline_status",
    "open_status",
    "overrun",
    "parse_tokens",
    "work_day",
]
