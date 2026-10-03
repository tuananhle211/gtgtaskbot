"""Time helpers.

Rule: the database stores timezone-aware UTC. Conversion to
``Asia/Ho_Chi_Minh`` happens only at presentation boundaries (Telegram
messages, reports, API responses meant for humans).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo


def utcnow() -> datetime:
    """Current time as a timezone-aware UTC datetime."""
    return datetime.now(UTC)


def utc_in(seconds: int) -> datetime:
    """UTC timestamp ``seconds`` in the future."""
    return utcnow() + timedelta(seconds=seconds)


def ensure_utc(value: datetime) -> datetime:
    """Coerce ``value`` to aware UTC, assuming UTC for naive input.

    Naive datetimes only reach this function when they come back from a driver
    that dropped the offset; assuming UTC matches how we store them.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def to_local(value: datetime, tz: ZoneInfo) -> datetime:
    """Render a stored UTC timestamp in the display timezone."""
    return ensure_utc(value).astimezone(tz)


def format_local(value: datetime, tz: ZoneInfo, fmt: str = "%Y-%m-%d %H:%M:%S %Z") -> str:
    """Human-readable local timestamp for Telegram / reports."""
    return to_local(value, tz).strftime(fmt)
