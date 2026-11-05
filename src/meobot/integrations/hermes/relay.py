"""Talking to the Hermes agents through the relay on their VPS (Đội agent, 0091).

The relay (``/opt/apexmed/agent-relay.py`` on the agents' VPS) holds every
agent's Hermes key and forwards ``POST /agents/<slug>/chat`` to Hermes'
OpenAI-compatible ``/p/<slug>/v1/chat/completions``, passing the SSE stream
through untouched. APM sends only the newest question: Hermes keeps the
transcript itself, keyed by ``X-Hermes-Session-Id``.

The stream is read here into :class:`RelayDelta` (a piece of the answer) and
:class:`RelayTool` (the agent started a tool) events. Comment lines
(``: keepalive``, every 10 s) are skipped.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from meobot.core.config import Settings

#: Hermes sends a keepalive every 10 s, so a long silence means a dead link.
READ_TIMEOUT_SECONDS = 60.0
CONNECT_TIMEOUT_SECONDS = 10.0


class RelayError(Exception):
    """The relay or Hermes failed. ``code`` is what the message row keeps."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


@dataclass(frozen=True, slots=True)
class RelayDelta:
    text: str


@dataclass(frozen=True, slots=True)
class RelayTool:
    tool: str
    label: str
    emoji: str = ""


RelayEvent = RelayDelta | RelayTool


class AgentRelay(Protocol):
    @property
    def configured(self) -> bool: ...

    def stream_chat(
        self,
        slug: str,
        *,
        text: str,
        session_id: str,
        session_key: str,
        idempotency_key: str,
    ) -> AsyncIterator[RelayEvent]: ...


def parse_sse(lines: Iterable[str]) -> list[RelayEvent]:
    """The events in whole SSE frames (for tests and the reader below)."""
    parser = SseParser()
    events: list[RelayEvent] = []
    for line in lines:
        events.extend(parser.feed(line))
    events.extend(parser.feed(""))
    return events


class SseParser:
    """Line-at-a-time SSE reader for Hermes' Chat Completions stream."""

    def __init__(self) -> None:
        self._event: str | None = None
        self._data: list[str] = []
        self.finished = False

    def feed(self, line: str) -> list[RelayEvent]:
        line = line.rstrip("\r")
        if line.startswith(":"):
            return []
        if line == "":
            return self._dispatch()
        name, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if name == "event":
            self._event = value
        elif name == "data":
            self._data.append(value)
        return []

    def _dispatch(self) -> list[RelayEvent]:
        event, data = self._event, "\n".join(self._data)
        self._event, self._data = None, []
        if not data:
            return []
        if data.strip() == "[DONE]":
            self.finished = True
            return []
        try:
            payload: Any = json.loads(data)
        except ValueError:
            return []
        if not isinstance(payload, dict):
            return []
        if event == "hermes.tool.progress":
            if payload.get("status") != "running" or not payload.get("tool"):
                return []
            return [
                RelayTool(
                    tool=str(payload["tool"])[:60],
                    label=str(payload.get("label") or payload["tool"])[:160],
                    emoji=str(payload.get("emoji") or "")[:8],
                )
            ]
        if event not in (None, "", "message"):
            return []
        error = payload.get("error")
        if isinstance(error, dict):
            raise RelayError("agent_error", str(error.get("message") or "")[:200])
        out: list[RelayEvent] = []
        for choice in payload.get("choices") or []:
            delta = (choice or {}).get("delta") or {}
            piece = delta.get("content")
            if isinstance(piece, str) and piece:
                out.append(RelayDelta(piece))
        return out


class HttpAgentRelay:
    """The real relay, over HTTP (through the SSH tunnel)."""

    def __init__(
        self, base_url: str | None, token: str | None, *, client: httpx.AsyncClient | None = None
    ) -> None:
        self._base_url = (base_url or "").rstrip("/")
        self._token = token or ""
        self._client = client

    @classmethod
    def from_settings(cls, settings: Settings) -> HttpAgentRelay:
        token = (
            settings.hermes_relay_token.get_secret_value() if settings.hermes_relay_token else None
        )
        return cls(settings.hermes_relay_url, token)

    @property
    def configured(self) -> bool:
        return bool(self._base_url and self._token)

    async def stream_chat(
        self,
        slug: str,
        *,
        text: str,
        session_id: str,
        session_key: str,
        idempotency_key: str,
    ) -> AsyncIterator[RelayEvent]:
        if not self.configured:
            raise RelayError("agents_not_configured")
        body = {
            "model": slug,
            "stream": True,
            "messages": [{"role": "user", "content": text}],
        }
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "text/event-stream",
            "X-Hermes-Session-Id": session_id,
            "X-Hermes-Session-Key": session_key,
            "Idempotency-Key": idempotency_key,
        }
        timeout = httpx.Timeout(READ_TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS)
        client = self._client or httpx.AsyncClient(timeout=timeout)
        try:
            async with client.stream(
                "POST",
                f"{self._base_url}/agents/{slug}/chat",
                json=body,
                headers=headers,
                timeout=timeout,
            ) as response:
                if response.status_code == 401:
                    raise RelayError("agent_relay_refused")
                if response.status_code == 404:
                    raise RelayError("agent_not_on_relay")
                if response.status_code >= 400:
                    raise RelayError("agent_error", f"HTTP {response.status_code}")
                parser = SseParser()
                async for line in response.aiter_lines():
                    for event in parser.feed(line):
                        yield event
                for event in parser.feed(""):
                    yield event
        except httpx.TimeoutException as exc:
            raise RelayError("agent_timeout") from exc
        except httpx.HTTPError as exc:
            raise RelayError("agent_unreachable") from exc
        finally:
            if self._client is None:
                await client.aclose()


class FakeAgentRelay:
    """Answers from a script (tests, local work without the VPS)."""

    def __init__(
        self,
        answers: list[list[RelayEvent]] | None = None,
        *,
        fail: str | None = None,
        configured: bool = True,
    ) -> None:
        self.answers = (
            answers
            if answers is not None
            else [[RelayDelta("Xin chào, "), RelayDelta("em là agent.")]]
        )
        self.fail = fail
        self._configured = configured
        self.calls: list[dict[str, str]] = []

    @property
    def configured(self) -> bool:
        return self._configured

    async def stream_chat(
        self,
        slug: str,
        *,
        text: str,
        session_id: str,
        session_key: str,
        idempotency_key: str,
    ) -> AsyncIterator[RelayEvent]:
        self.calls.append(
            {
                "slug": slug,
                "text": text,
                "session_id": session_id,
                "session_key": session_key,
                "idempotency_key": idempotency_key,
            }
        )
        if not self._configured:
            raise RelayError("agents_not_configured")
        script = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        for event in script:
            yield event
        if self.fail:
            raise RelayError(self.fail)


__all__ = [
    "AgentRelay",
    "FakeAgentRelay",
    "HttpAgentRelay",
    "RelayDelta",
    "RelayError",
    "RelayEvent",
    "RelayTool",
    "SseParser",
    "parse_sse",
]
