"""Sending one queued message, and understanding why it failed.

Two jobs, kept together because they are the same boundary:

* :class:`DeliveryFailureMapper` turns whatever Telegram said into one of a
  small set of categories the application can act on. The provider's own words
  never travel further than a log line - they can echo message content, and
  nothing downstream needs more than "retry", "this recipient is unreachable"
  or "stop".
* :class:`TelegramDeliveryService` renders one stored payload and hands it to
  the injected transport. It does not decide *whether* to send: the router
  already did that, before the row existed.

The transport is the existing :class:`~meobot.integrations.telegram.notifier.Notifier`
protocol, so tests substitute a recorder and no test can reach Telegram.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from meobot.core.logging import get_logger
from meobot.db.models.notifications import OutboundMessage
from meobot.domain.notifications.models import FailureCategory
from meobot.domain.notifications.templates import render
from meobot.integrations.telegram.notifier import Notifier

logger = get_logger(__name__)

#: Telegram's own phrasing, matched loosely because it varies by API version.
_PATTERNS: tuple[tuple[re.Pattern[str], FailureCategory], ...] = (
    (re.compile(r"bot was blocked by the user", re.I), FailureCategory.PRIVATE_CHAT_UNAVAILABLE),
    (re.compile(r"user is deactivated", re.I), FailureCategory.PRIVATE_CHAT_UNAVAILABLE),
    (re.compile(r"can't initiate conversation", re.I), FailureCategory.PRIVATE_CHAT_UNAVAILABLE),
    (re.compile(r"bot can't initiate", re.I), FailureCategory.PRIVATE_CHAT_UNAVAILABLE),
    (re.compile(r"chat not found", re.I), FailureCategory.CHAT_NOT_FOUND),
    (re.compile(r"bot is not a member", re.I), FailureCategory.BOT_NOT_IN_CHAT),
    (re.compile(r"bot was kicked", re.I), FailureCategory.BOT_NOT_IN_CHAT),
    (re.compile(r"group chat was upgraded", re.I), FailureCategory.CHAT_NOT_FOUND),
    (re.compile(r"not enough rights", re.I), FailureCategory.BOT_CANNOT_SEND),
    (re.compile(r"have no rights to send", re.I), FailureCategory.BOT_CANNOT_SEND),
    (re.compile(r"chat_write_forbidden", re.I), FailureCategory.BOT_CANNOT_SEND),
    (re.compile(r"too many requests|retry after", re.I), FailureCategory.RATE_LIMITED),
    (re.compile(r"timeout|timed out", re.I), FailureCategory.NETWORK),
    (re.compile(r"connection|network|reset by peer", re.I), FailureCategory.NETWORK),
    (
        re.compile(r"bad gateway|service unavailable|internal server error", re.I),
        FailureCategory.PROVIDER_UNAVAILABLE,
    ),
)

_RETRY_AFTER = re.compile(r"retry after (\d+)", re.I)


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    """What one send attempt achieved."""

    delivered: bool
    category: FailureCategory = FailureCategory.NONE
    retry_after_seconds: int | None = None
    telegram_message_id: int | None = None


class DeliveryFailureMapper:
    """Classifies a provider failure without leaking what it said."""

    @staticmethod
    def categorize(error: BaseException | str | None) -> FailureCategory:
        """Map an exception or message to an actionable category."""
        if error is None:
            return FailureCategory.NONE
        text = str(error)
        for pattern, category in _PATTERNS:
            if pattern.search(text):
                return category
        if isinstance(error, TimeoutError):
            return FailureCategory.NETWORK
        return FailureCategory.UNKNOWN

    @staticmethod
    def retry_after(error: BaseException | str | None) -> int | None:
        """Telegram's own requested delay, when it gave one.

        Honouring it matters: arguing with a rate limiter is how a short limit
        becomes a long one.
        """
        if error is None:
            return None
        found = _RETRY_AFTER.search(str(error))
        return int(found.group(1)) if found else None


class TelegramDeliveryService:
    """Renders and sends one queued message through the injected transport.

    Args:
        notifier: Any :class:`~meobot.integrations.telegram.notifier.Notifier`.
            Tests pass a recorder; the worker passes the real HTTP client.
    """

    def __init__(self, notifier: Notifier) -> None:
        self._notifier = notifier

    async def deliver(
        self, message: OutboundMessage, *, reply_markup: dict[str, Any] | None = None
    ) -> DeliveryResult:
        """Send one message. Never raises.

        A delivery failure is data, not an exception: the caller has to record
        it against the outbox row and decide about a retry, and an exception
        escaping here would abort a batch that still has good messages in it.

        Args:
            reply_markup: Buttons rebuilt from the business record immediately
                before this call - see
                :mod:`~meobot.application.outbound_keyboards`. They are not
                stored on the row because a signature minted at queue time
                would be stale by the time a retry delivered it.
        """
        try:
            text = render(message.template_key, dict(message.safe_payload_json))
        except (KeyError, ValueError) as exc:
            # A payload that no longer matches its template cannot be fixed by
            # retrying, and is a programming error worth surfacing loudly.
            logger.error(
                "outbound_message_unrenderable",
                extra={
                    "outbound_message_id": str(message.id),
                    "template_key": message.template_key,
                    "error": type(exc).__name__,
                },
            )
            return DeliveryResult(delivered=False, category=FailureCategory.DESTINATION_REFUSED)

        try:
            accepted = await self._notifier.send(
                message.telegram_chat_id, text, reply_markup=reply_markup
            )
        except Exception as exc:
            category = DeliveryFailureMapper.categorize(exc)
            logger.warning(
                "outbound_message_send_failed",
                extra={
                    "outbound_message_id": str(message.id),
                    "category": category.value,
                    # The provider's text stays here, in a log the operator
                    # reads, and goes no further.
                    "error": str(exc)[:200],
                },
            )
            return DeliveryResult(
                delivered=False,
                category=category,
                retry_after_seconds=DeliveryFailureMapper.retry_after(exc),
            )

        if accepted:
            return DeliveryResult(delivered=True)

        # The notifier swallows its own errors and returns False. Without an
        # exception to read there is nothing to classify, so it is treated as
        # retryable - the conservative choice for an unknown failure.
        logger.info(
            "outbound_message_not_accepted",
            extra={"outbound_message_id": str(message.id)},
        )
        return DeliveryResult(delivered=False, category=FailureCategory.UNKNOWN)
