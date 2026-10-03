"""Outbound-only Telegram integration used by background workers.

The bot process itself uses aiogram; this package exists so a Celery task can
report a finished review without owning a dispatcher.
"""

from meobot.integrations.telegram.notifier import (
    FakeNotifier,
    Notifier,
    NullNotifier,
    TelegramNotifier,
    build_notifier,
)

__all__ = [
    "FakeNotifier",
    "Notifier",
    "NullNotifier",
    "TelegramNotifier",
    "build_notifier",
]
