"""Talking to the Hermes agents through the relay on their VPS (Đội agent, 0091).

The relay (``/opt/apexmed/agent-relay.py`` on the agents' VPS) holds every
agent's Hermes key and forwards ``POST /agents/<slug>/chat`` to Hermes'
OpenAI-compatible ``/p/<slug>/v1/chat/completions``, passing the SSE stream
through untouched. APM sends only the newest question: Hermes keeps the
transcript itself, keyed by ``X-Hermes-Session-Id``.

The stream is read here into :class:`RelayDelta` (a piece of the answer) and
:class:`RelayTool` (the agent started a tool) events. Comment lines
(``: keepalive``, every 10 s) are skipped.

``GET /skills`` lists every agent's skills (read from files on the VPS, no
Hermes call): approved ones from the Second Brain (``kho``), the agent's own
not yet approved (``rieng``), Hermes' bundled ones (``co-san``). Kept a few
minutes here so the agents screen does not ask the VPS on every load.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import AsyncIterator, Iterable, Sequence
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

#: Where a skill comes from (relay ``source``).
SKILL_SOURCES = ("kho", "rieng", "co-san")
#: How long the skill list is kept before the VPS is asked again.
SKILLS_TTL_SECONDS = 120.0


@dataclass(frozen=True, slots=True)
class AgentSkill:
    name: str
    description: str
    source: str
    group: str | None = None


#: slug -> that agent's skills.
SkillBook = dict[str, list[AgentSkill]]


def parse_skills(payload: object) -> SkillBook:
    """The relay's ``{"agents": {slug: [...]}}``, anything odd skipped."""
    book: SkillBook = {}
    agents = payload.get("agents") if isinstance(payload, dict) else None
    if not isinstance(agents, dict):
        return book
    for slug, items in agents.items():
        if not isinstance(slug, str) or not isinstance(items, list):
            continue
        skills: list[AgentSkill] = []
        for item in items:
            if not isinstance(item, dict) or item.get("source") not in SKILL_SOURCES:
                continue
            name = str(item.get("name") or "").strip()[:80]
            if not name:
                continue
            group = item.get("group")
            skills.append(
                AgentSkill(
                    name=name,
                    description=str(item.get("description") or "").strip()[:300],
                    source=str(item["source"]),
                    group=str(group)[:60] if isinstance(group, str) and group else None,
                )
            )
        book[slug] = skills
    return book


#: base url -> (when fetched, book); one API process, so a plain dict is enough.
_SKILL_CACHE: dict[str, tuple[float, SkillBook]] = {}


#: What a Hermes transcript id may look like (``apm-<uuid>``) before it goes in a URL.
_SESSION_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")


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
        images: Sequence[str] = (),
    ) -> AsyncIterator[RelayEvent]: ...

    async def delete_session(self, slug: str, session_id: str) -> bool: ...

    async def skills(self) -> SkillBook | None: ...


def chat_content(text: str, images: Sequence[str]) -> str | list[dict[str, Any]]:
    """The question as Hermes takes it: plain text, or text + ``image_url`` parts."""
    if not images:
        return text
    return [
        {"type": "text", "text": text},
        *({"type": "image_url", "image_url": {"url": url}} for url in images),
    ]


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
        images: Sequence[str] = (),
    ) -> AsyncIterator[RelayEvent]:
        if not self.configured:
            raise RelayError("agents_not_configured")
        body = {
            "model": slug,
            "stream": True,
            "messages": [{"role": "user", "content": chat_content(text, images)}],
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
                if response.status_code == 413:
                    raise RelayError("agent_request_too_large")
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

    async def delete_session(self, slug: str, session_id: str) -> bool:
        """Delete the Hermes transcript of a conversation; True = gone (or never there)."""
        if not self.configured or not _SESSION_ID.match(session_id):
            return False
        timeout = httpx.Timeout(20.0, connect=CONNECT_TIMEOUT_SECONDS)
        client = self._client or httpx.AsyncClient(timeout=timeout)
        try:
            response = await client.delete(
                f"{self._base_url}/agents/{slug}/sessions/{session_id}",
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=timeout,
            )
        except httpx.HTTPError:
            return False
        finally:
            if self._client is None:
                await client.aclose()
        return response.status_code in (200, 204, 404)

    async def skills(self) -> SkillBook | None:
        """Every agent's skills; None when the relay cannot say (down, or an older relay)."""
        if not self.configured:
            return None
        cached = _SKILL_CACHE.get(self._base_url)
        if cached and time.monotonic() - cached[0] < SKILLS_TTL_SECONDS:
            return cached[1]
        timeout = httpx.Timeout(15.0, connect=CONNECT_TIMEOUT_SECONDS)
        client = self._client or httpx.AsyncClient(timeout=timeout)
        try:
            response = await client.get(
                f"{self._base_url}/skills",
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=timeout,
            )
            if response.status_code != 200:
                return cached[1] if cached else None
            book = parse_skills(response.json())
        except (httpx.HTTPError, ValueError):
            return cached[1] if cached else None
        finally:
            if self._client is None:
                await client.aclose()
        _SKILL_CACHE[self._base_url] = (time.monotonic(), book)
        return book


class FakeAgentRelay:
    """Answers from a script (tests, local work without the VPS)."""

    def __init__(
        self,
        answers: list[list[RelayEvent]] | None = None,
        *,
        fail: str | None = None,
        configured: bool = True,
        skill_book: SkillBook | None = None,
    ) -> None:
        self.answers = (
            answers
            if answers is not None
            else [[RelayDelta("Xin chào, "), RelayDelta("em là agent.")]]
        )
        self.fail = fail
        self._configured = configured
        self.calls: list[dict[str, Any]] = []
        self.deleted: list[tuple[str, str]] = []
        self.skill_book = skill_book

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
        images: Sequence[str] = (),
    ) -> AsyncIterator[RelayEvent]:
        self.calls.append(
            {
                "slug": slug,
                "text": text,
                "session_id": session_id,
                "session_key": session_key,
                "idempotency_key": idempotency_key,
                "images": list(images),
            }
        )
        if not self._configured:
            raise RelayError("agents_not_configured")
        script = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        for event in script:
            yield event
        if self.fail:
            raise RelayError(self.fail)

    async def delete_session(self, slug: str, session_id: str) -> bool:
        self.deleted.append((slug, session_id))
        return self._configured

    async def skills(self) -> SkillBook | None:
        return self.skill_book if self._configured else None


__all__ = [
    "SKILL_SOURCES",
    "AgentRelay",
    "AgentSkill",
    "FakeAgentRelay",
    "HttpAgentRelay",
    "RelayDelta",
    "RelayError",
    "RelayEvent",
    "RelayTool",
    "SkillBook",
    "SseParser",
    "chat_content",
    "parse_skills",
    "parse_sse",
]
