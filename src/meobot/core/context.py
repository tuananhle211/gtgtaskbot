"""Request / correlation context propagated across API, bot and Celery."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token

_request_id: ContextVar[str | None] = ContextVar("meobot_request_id", default=None)


def new_request_id() -> str:
    """Generate a fresh correlation id."""
    return str(uuid.uuid4())


def get_request_id() -> str | None:
    """Return the correlation id bound to the current task/coroutine, if any."""
    return _request_id.get()


def require_request_id() -> str:
    """Return the current correlation id, generating one if none is bound."""
    current = _request_id.get()
    if current is None:
        current = new_request_id()
        _request_id.set(current)
    return current


def set_request_id(request_id: str) -> Token[str | None]:
    """Bind ``request_id`` to the current context. Caller owns the reset token."""
    return _request_id.set(request_id)


@contextmanager
def request_context(request_id: str | None = None) -> Iterator[str]:
    """Scope a correlation id to a block of work."""
    value = request_id or new_request_id()
    token = _request_id.set(value)
    try:
        yield value
    finally:
        _request_id.reset(token)
