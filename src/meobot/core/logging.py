"""Structured logging with secret redaction.

Two formatters are available: ``json`` (production, one object per line) and
``console`` (development). Both run every record through
:class:`RedactingFilter`, which removes bot tokens, API keys and OAuth refresh
tokens from messages and extras.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from typing import Any

from meobot.core.context import get_request_id

REDACTED = "***redacted***"

#: Keys whose values must never reach a log sink.
SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "token",
        "bot_token",
        "telegram_bot_token",
        "api_key",
        "llm_api_key",
        "apikey",
        "password",
        "postgres_password",
        "secret",
        "client_secret",
        "app_secret",
        "meta_app_secret",
        "tiktok_client_secret",
        "access_token",
        "refresh_token",
        # --- OAuth values in flight (Step 1F.2.6) ----------------------------
        # ``token``, ``access_token`` and ``refresh_token`` above cover what is
        # stored. These two are the names an authorization *callback* could
        # plausibly reach for: a one-time code is a bearer credential for the
        # seconds it lives, and a log line holding one can be replayed into
        # somebody's account.
        #
        # Deliberately **not** the bare name ``code``. It was tried and reverted:
        # ``code`` is the most overloaded field name in this codebase - a
        # platform code, a channel code, an error code, a capability code - and
        # redacting it turned an audit record of "somebody created the FACEBOOK
        # platform" into ``***redacted***``. A redaction that hides ordinary
        # business data is a redaction people learn to work around.
        #
        # Nothing logs an authorization code under the bare name anyway: the
        # connection service takes ``code`` as a parameter and never puts it in
        # a log call, and the TikTok client sends it in a form body it does not
        # log. These entries are the belt for that brace.
        "authorization_code",
        "code_verifier",
        "client_key",
        "oauth_refresh_token",
        "authorization",
        "cookie",
        "private_key",
        "service_account",
        "credentials",
        "database_url",
        "dsn",
        # --- Web admin session secrets (Step 1E.1) ---------------------------
        # ``token`` above already covers the common case. These are the names the
        # web auth path could plausibly reach for, listed explicitly so that
        # adding one to a log call is a no-op rather than a leak.
        "session_token",
        "login_token",
        "token_hash",
        "magic_link",
        "login_url",
        "set_cookie",
        "session_cookie",
        # --- Web password login (0045) ---------------------------------------
        # ``password`` above covers the request field. These are the other
        # names the login, change and reset paths use.
        "current_password",
        "new_password",
        "password_hash",
        "web_default_password",
        # --- Password reset (0046) ------------------------------------------
        "temporary_password",
    }
)

_TOKEN_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Telegram bot token: 123456789:AA...
    re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b"),
    # Generic bearer tokens
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{16,}"),
    # DSN passwords: scheme://user:password@host
    re.compile(r"(?i)\b([a-z0-9+]+://[^:\s/]+):([^@\s]+)@"),
)

_RESERVED_ATTRS: frozenset[str] = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__.keys()
) | {"message", "asctime", "taskName"}


def redact_text(text: str) -> str:
    """Mask token-shaped substrings inside free-form text."""
    result = text
    for pattern in _TOKEN_PATTERNS:
        if pattern.groups == 2:
            result = pattern.sub(rf"\1:{REDACTED}@", result)
        else:
            result = pattern.sub(REDACTED, result)
    return result


def redact_value(key: str, value: Any) -> Any:
    """Redact ``value`` when ``key`` looks sensitive; recurse into containers."""
    if key.lower() in SENSITIVE_KEYS:
        return REDACTED
    if isinstance(value, dict):
        return {k: redact_value(str(k), v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact_value(key, item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def redact_mapping(data: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``data`` safe to persist or log."""
    return {key: redact_value(str(key), value) for key, value in data.items()}


class RedactingFilter(logging.Filter):
    """Scrub secrets from the message and from ``extra`` fields."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_text(record.msg)
        for key, value in list(record.__dict__.items()):
            if key in _RESERVED_ATTRS:
                continue
            record.__dict__[key] = redact_value(key, value)
        return True


class RequestIdFilter(logging.Filter):
    """Attach the current correlation id to every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line - friendly to ``docker logs`` and log shippers."""

    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "service": self.service,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = getattr(record, "request_id", None)
        if request_id:
            payload["request_id"] = request_id
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key in _RESERVED_ATTRS or key in payload or key == "request_id":
                continue
            payload[key] = value
        return json.dumps(payload, ensure_ascii=False, default=str)


class ConsoleFormatter(logging.Formatter):
    """Compact human-readable output for local development."""

    def __init__(self, service: str) -> None:
        super().__init__("%(asctime)s %(levelname)-7s %(name)s | %(message)s", "%H:%M:%S")
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        request_id = getattr(record, "request_id", None)
        suffix = f" [req={request_id[:8]}]" if isinstance(request_id, str) else ""
        return f"{base}{suffix}"


def configure_logging(
    *,
    service: str,
    level: str = "INFO",
    log_format: str = "json",
) -> None:
    """Install MeoBot's logging configuration on the root logger.

    Idempotent: existing handlers are replaced, so calling it from each
    container entry point is safe.
    """
    formatter: logging.Formatter
    formatter = JsonFormatter(service) if log_format == "json" else ConsoleFormatter(service)

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(formatter)
    handler.addFilter(RequestIdFilter())
    handler.addFilter(RedactingFilter())

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level.upper())

    # Third-party loggers we do not want at DEBUG even when we are.
    for noisy in ("aiogram.event", "asyncio", "aiosqlite", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(max(logging.INFO, root.level))


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced logger."""
    return logging.getLogger(name)
