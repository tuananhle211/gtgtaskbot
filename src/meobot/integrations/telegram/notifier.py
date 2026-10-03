"""Outbound Telegram messages from processes that are not the bot.

A Celery worker finishing a review has to tell the Owner about it, but it does
not run aiogram's dispatcher. This is the smallest possible sender: one HTTPS
POST to ``sendMessage``, with the same timeout/retry discipline as every other
integration, and no ability to receive anything.

The token is never logged; :mod:`meobot.core.logging` also masks token-shaped
strings, so even an accidental interpolation into a message is redacted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import httpx

from meobot.core.errors import IntegrationError, IntegrationNotConfiguredError
from meobot.core.logging import get_logger
from meobot.integrations.base import IntegrationConfig, call_with_retries

logger = get_logger(__name__)

PROVIDER = "telegram"
API_BASE = "https://api.telegram.org"

#: Telegram rejects messages longer than this.
MAX_MESSAGE_LENGTH = 4096


@runtime_checkable
class Notifier(Protocol):
    """Sends a text message to one Telegram chat."""

    async def send(
        self,
        chat_id: int,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
    ) -> bool:
        """Send ``text``; return True when Telegram accepted it."""
        ...


@dataclass(frozen=True, slots=True)
class ChatProbe:
    """What a read-only look at a destination found.

    Deliberately not a boolean. "The bot cannot post here" and "Telegram did
    not answer" are different facts, and treating the second as the first would
    disable every healthy group during a brief outage.
    """

    exists: bool
    bot_is_member: bool
    can_send: bool
    is_admin: bool
    chat_type: str = ""
    title: str | None = None
    #: True when the probe itself failed - the destination is not implicated.
    provider_error: bool = False


@runtime_checkable
class HealthProbe(Protocol):
    """Reads a destination's state without sending anything to it.

    Separate from :class:`Notifier` on purpose. Health checking is a different
    capability from sending, most senders do not have it, and widening
    ``Notifier`` would force every implementation - including the null one - to
    grow a method it cannot honour.

    **Never sends a visible message.** A "test message" in a real group full of
    real colleagues is not a health check, it is a notification nobody asked
    for. ``getChat`` and ``getChatMember`` read state and produce nothing.
    """

    async def probe_chat(self, chat_id: int) -> ChatProbe:
        """Look at one destination and report what is true of it."""
        ...


@dataclass
class FakeNotifier:
    """Collects messages instead of sending them. Used by tests."""

    sent: list[tuple[int, str]] = field(default_factory=list)
    markups: list[dict[str, Any] | None] = field(default_factory=list)
    #: Canned probe answers, keyed by chat id. Anything absent reads healthy.
    probes: dict[int, ChatProbe] = field(default_factory=dict)
    #: Every chat this fake was asked about, so a test can assert that probing
    #: happened *and* that nothing was sent while it did.
    probed: list[int] = field(default_factory=list)

    async def send(
        self,
        chat_id: int,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
    ) -> bool:
        self.sent.append((chat_id, text))
        self.markups.append(reply_markup)
        return True

    async def probe_chat(self, chat_id: int) -> ChatProbe:
        self.probed.append(chat_id)
        return self.probes.get(
            chat_id,
            ChatProbe(
                exists=True,
                bot_is_member=True,
                can_send=True,
                is_admin=False,
                chat_type="supergroup",
            ),
        )


class NullNotifier:
    """Drops messages with a log line.

    Used when no bot token is configured: a worker must still finish its job
    when nobody can be notified.
    """

    async def send(
        self,
        chat_id: int,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
    ) -> bool:
        logger.warning("telegram_notification_dropped", extra={"chat_id": chat_id})
        return False


class TelegramNotifier:
    """Real ``sendMessage`` client.

    Args:
        bot_token: Secret. Used only to build the request URL.
        config: Timeout and retry budget.
        client: Injected ``httpx.AsyncClient`` for tests.
    """

    def __init__(
        self,
        bot_token: str,
        *,
        config: IntegrationConfig | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not bot_token:
            raise IntegrationNotConfiguredError(
                "TELEGRAM_BOT_TOKEN is required to send notifications", provider=PROVIDER
            )
        self._token = bot_token
        self._config = config or IntegrationConfig(provider=PROVIDER)
        self._client = client
        self._owns_client = client is None
        self._bot_id_cache: int | None = None

    async def aclose(self) -> None:
        """Close the HTTP client when this instance created it."""
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def send(
        self,
        chat_id: int,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
    ) -> bool:
        """Send a message, truncating it to Telegram's limit.

        Returns False instead of raising when Telegram refuses: a failed
        notification must not roll back the work it was announcing.
        """
        body: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text[:MAX_MESSAGE_LENGTH],
            "disable_web_page_preview": True,
        }
        if reply_markup is not None:
            body["reply_markup"] = reply_markup

        async def attempt() -> bool:
            client = self._ensure_client()
            response = await client.post(
                f"{API_BASE}/bot{self._token}/sendMessage",
                json=body,
            )
            if response.status_code == httpx.codes.OK:
                return True
            raise IntegrationError(
                f"Telegram sendMessage failed with HTTP {response.status_code}",
                provider=PROVIDER,
                details={"status": response.status_code, "chat_id": chat_id},
            )

        try:
            return await call_with_retries(
                attempt, config=self._config, operation_name="telegram.send_message"
            )
        except (IntegrationError, httpx.HTTPError) as exc:
            logger.warning(
                "telegram_notification_failed",
                extra={"chat_id": chat_id, "error": type(exc).__name__},
            )
            return False

    async def probe_chat(self, chat_id: int) -> ChatProbe:
        """Read one destination's state with ``getChat`` and ``getChatMember``.

        Two calls, neither of which produces a message. ``getChat`` answers
        "does this still exist"; ``getChatMember`` for the bot's own id answers
        "am I still in it, and may I post".

        A transport failure returns ``provider_error=True`` rather than an
        unhealthy verdict. Telegram being briefly unreachable says nothing
        about whether a group exists, and the caller must not disable a working
        destination because of it.
        """
        try:
            chat = await self._get("getChat", {"chat_id": chat_id})
        except Exception as exc:
            return self._probe_failure(chat_id, exc)
        if chat is None:
            return ChatProbe(exists=False, bot_is_member=False, can_send=False, is_admin=False)

        chat_type = str(chat.get("type") or "")
        title = chat.get("title")

        try:
            member = await self._get(
                "getChatMember", {"chat_id": chat_id, "user_id": await self._bot_id()}
            )
        except Exception as exc:
            return self._probe_failure(chat_id, exc)
        if member is None:
            return ChatProbe(
                exists=True,
                bot_is_member=False,
                can_send=False,
                is_admin=False,
                chat_type=chat_type,
                title=title,
            )

        status = str(member.get("status") or "")
        is_member = status not in {"left", "kicked"}
        is_admin = status in {"administrator", "creator"}
        # ``can_send_messages`` is absent for administrators and for ordinary
        # members of an unrestricted group; absent means "not restricted".
        can_send = is_member and bool(member.get("can_send_messages", True))
        return ChatProbe(
            exists=True,
            bot_is_member=is_member,
            can_send=can_send and is_member,
            is_admin=is_admin,
            chat_type=chat_type,
            title=title,
        )

    async def _bot_id(self) -> int:
        """This bot's own Telegram id, read from the token and cached."""
        if self._bot_id_cache is None:
            me = await self._get("getMe", {})
            self._bot_id_cache = int(me["id"]) if me else 0
        return self._bot_id_cache

    async def _get(self, method: str, params: dict[str, Any]) -> dict[str, Any] | None:
        """One read-only Bot API call. ``None`` means Telegram said "no such thing"."""
        client = self._ensure_client()
        response = await client.post(f"{API_BASE}/bot{self._token}/{method}", json=params)
        if response.status_code == httpx.codes.OK:
            body = response.json()
            result = body.get("result")
            return result if isinstance(result, dict) else None
        if response.status_code in {httpx.codes.BAD_REQUEST, httpx.codes.FORBIDDEN}:
            # "chat not found", "bot was kicked": a definite answer about the
            # destination, not a transport problem.
            return None
        raise IntegrationError(
            f"Telegram {method} failed with HTTP {response.status_code}",
            provider=PROVIDER,
            details={"status": response.status_code},
        )

    @staticmethod
    def _probe_failure(chat_id: int, exc: BaseException) -> ChatProbe:
        logger.warning(
            "telegram_probe_failed",
            extra={"chat_id": chat_id, "error": type(exc).__name__},
        )
        return ChatProbe(
            exists=True,
            bot_is_member=True,
            can_send=True,
            is_admin=False,
            provider_error=True,
        )

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._config.timeout_seconds)
        return self._client


def build_notifier(bot_token: str | None) -> Notifier:
    """Return a real notifier when a token exists, otherwise a no-op one."""
    if not bot_token:
        return NullNotifier()
    return TelegramNotifier(bot_token)
