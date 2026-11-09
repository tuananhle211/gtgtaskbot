"""Pancake's Page API (https://developer.pancake.biz, 0094).

* ``GET  /v1/pages/{page}/conversations/{conv}/messages`` - the thread (30 last);
* ``POST /v1/pages/{page}/conversations/{conv}/messages`` ``{"action":
  "reply_inbox", "message": ...}`` - send; HTTP 200 does not mean sent, the body's
  ``success`` does;
* ``GET  /v2/pages/{page}/conversations?fields=id`` - used only to check a token.

The page token goes in the query string (``page_access_token``) as Pancake
asks; it is never logged here (httpx logs no query at INFO, and errors keep
only a code).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from meobot.domain.pancake.models import (
    DEFAULT_API_BASE,
    PancakeError,
    Thread,
    ThreadMessage,
    parse_thread,
)

TIMEOUT_SECONDS = 20.0


class PancakeClient(Protocol):
    async def thread(self, page_id: str, conversation_id: str, token: str) -> Thread: ...

    async def send(self, page_id: str, conversation_id: str, token: str, text: str) -> str: ...

    async def check(self, page_id: str, token: str) -> int: ...


def _payload(response: httpx.Response) -> dict[str, Any]:
    if response.status_code in (401, 403):
        raise PancakeError("pancake_token_invalid", "Token page sai hoặc đã bị đổi.")
    if response.status_code == 429:
        raise PancakeError("pancake_rate_limited", "Pancake đang giới hạn số lần gọi.")
    if response.status_code >= 400:
        raise PancakeError("pancake_failed", f"Pancake trả lỗi HTTP {response.status_code}.")
    try:
        payload = response.json()
    except ValueError as error:
        raise PancakeError("pancake_failed", "Pancake trả về dữ liệu lạ.") from error
    if not isinstance(payload, dict):
        raise PancakeError("pancake_failed", "Pancake trả về dữ liệu lạ.")
    if payload.get("success") is False:
        code = str(payload.get("message_code") or payload.get("error_code") or "")
        text = str(payload.get("message") or "")[:200]
        if "token" in (code + text).lower():
            raise PancakeError("pancake_token_invalid", "Token page sai hoặc đã bị đổi.")
        raise PancakeError(
            "pancake_failed", f"Pancake từ chối: {text or code or 'không rõ lý do'}."
        )
    return payload


class HttpPancakeClient:
    def __init__(self, base_url: str = DEFAULT_API_BASE, *, timeout: float = TIMEOUT_SECONDS):
        self._base = base_url.rstrip("/")
        self._timeout = timeout

    async def _request(
        self,
        method: str,
        path: str,
        token: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        query = {"page_access_token": token, **(params or {})}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.request(method, self._base + path, params=query, json=json)
        except httpx.HTTPError as error:
            raise PancakeError("pancake_unreachable", "Không kết nối được Pancake.") from error
        return _payload(response)

    async def thread(self, page_id: str, conversation_id: str, token: str) -> Thread:
        payload = await self._request(
            "GET", f"/v1/pages/{page_id}/conversations/{conversation_id}/messages", token
        )
        return parse_thread(payload, page_id)

    async def send(self, page_id: str, conversation_id: str, token: str, text: str) -> str:
        payload = await self._request(
            "POST",
            f"/v1/pages/{page_id}/conversations/{conversation_id}/messages",
            token,
            json={"action": "reply_inbox", "message": text},
        )
        return str(payload.get("id") or "")[:160]

    async def check(self, page_id: str, token: str) -> int:
        payload = await self._request(
            "GET", f"/v2/pages/{page_id}/conversations", token, params={"fields": "id"}
        )
        conversations = payload.get("conversations")
        return len(conversations) if isinstance(conversations, list) else 0


@dataclass
class FakePancakeClient:
    """Pancake from a script (tests, local work)."""

    threads: dict[str, Thread] = field(default_factory=dict)
    fail_send: str | None = None
    fail_thread: str | None = None
    sent: list[dict[str, str]] = field(default_factory=list)
    checked: list[str] = field(default_factory=list)

    def say(self, conversation_id: str, *messages: ThreadMessage, can_inbox: bool = True) -> None:
        old = self.threads.get(conversation_id, Thread())
        self.threads[conversation_id] = Thread(
            messages=old.messages + tuple(messages), can_inbox=can_inbox
        )

    async def thread(self, page_id: str, conversation_id: str, token: str) -> Thread:
        if self.fail_thread:
            raise PancakeError(self.fail_thread)
        return self.threads.get(conversation_id, Thread())

    async def send(self, page_id: str, conversation_id: str, token: str, text: str) -> str:
        if self.fail_send:
            raise PancakeError(self.fail_send)
        message_id = f"sent-{len(self.sent) + 1}"
        self.sent.append(
            {
                "page_id": page_id,
                "conversation_id": conversation_id,
                "token": token,
                "text": text,
                "id": message_id,
            }
        )
        return message_id

    async def check(self, page_id: str, token: str) -> int:
        self.checked.append(page_id)
        if token == "bad":  # noqa: S105 - the fake's refused token
            raise PancakeError("pancake_token_invalid", "Token page sai hoặc đã bị đổi.")
        return 3


__all__ = ["FakePancakeClient", "HttpPancakeClient", "PancakeClient"]
