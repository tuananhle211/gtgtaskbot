"""Secrets in third-party log lines (03/11 - 09/11).

httpx logs every request as ``"HTTP Request: %s %s ..."`` with the URL as an
argument: the Telegram bot token (in the path, glued to ``bot``) and Meta's
``access_token`` (in the paging links' query) reached the worker's log because
the filter only looked at the format string.
"""

from __future__ import annotations

import logging
import sys

from meobot.core.logging import REDACTED, JsonFormatter, RedactingFilter, redact_text

BOT_TOKEN = "8123456789:AAFakeTokenForTestsOnly_abcdefghijklmno"
META_TOKEN = "EAAGfakeMetaTokenForTests1234567890"


def _rendered(msg: str, *args: object, exc_info: object = None) -> str:
    record = logging.LogRecord(
        name="httpx",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=args or None,
        exc_info=exc_info,  # type: ignore[arg-type]
    )
    assert RedactingFilter().filter(record) is True
    return JsonFormatter("test").format(record)


def test_01_the_bot_token_in_an_httpx_request_line_is_masked() -> None:
    line = _rendered(
        'HTTP Request: %s %s "%s %d %s"',
        "POST",
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        "HTTP/1.1",
        200,
        "OK",
    )
    assert BOT_TOKEN not in line
    assert BOT_TOKEN.split(":")[1] not in line
    assert "api.telegram.org/bot" in line and "sendMessage" in line and REDACTED in line


def test_02_a_token_in_the_query_string_is_masked_and_the_rest_kept() -> None:
    url = (
        "https://graph.facebook.com/v26.0/act_1/activities"
        f"?limit=500&access_token={META_TOKEN}&after=cursor-2"
    )
    line = _rendered("HTTP Request: %s %s", "GET", url)
    assert META_TOKEN not in line
    assert "limit=500" in line and "after=cursor-2" in line
    assert f"access_token={REDACTED}" in line
    assert redact_text("https://x.vn/api?page_access_token=abc123&page=2") == (
        f"https://x.vn/api?page_access_token={REDACTED}&page=2"
    )


def test_03_an_exception_naming_the_url_is_masked_too() -> None:
    try:
        raise RuntimeError(f"Server error for url 'https://api.telegram.org/bot{BOT_TOKEN}/getMe'")
    except RuntimeError:
        line = _rendered("telegram call failed", exc_info=sys.exc_info())
    assert BOT_TOKEN not in line and REDACTED in line


def test_04_ordinary_lines_are_left_alone() -> None:
    line = _rendered("Task %s succeeded in %ss: %s", "ads.sync_spend", 81, {"rows": 113})
    assert "ads.sync_spend" in line and "81" in line and "113" in line
    assert REDACTED not in line
    # A broken format string still logs, its arguments redacted too.
    odd = _rendered("odd %d", f"https://api.telegram.org/bot{BOT_TOKEN}/getMe")
    assert "odd %d" in odd and BOT_TOKEN not in odd
