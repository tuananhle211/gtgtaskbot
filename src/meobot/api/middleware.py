"""ASGI middleware: correlation ids and access logging."""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from meobot.core.context import new_request_id, request_context
from meobot.core.logging import get_logger

logger = get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Bind a correlation id per request and log the outcome.

    An inbound ``X-Request-ID`` is honoured when it is a valid UUID, so a call
    chain keeps one id end to end; otherwise a fresh one is minted.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER)
        request_id = _coerce_uuid(incoming) or new_request_id()

        started = time.perf_counter()
        with request_context(request_id):
            response = await call_next(request)
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            response.headers[REQUEST_ID_HEADER] = request_id
            logger.info(
                "http_request",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": duration_ms,
                },
            )
            return response


def _coerce_uuid(value: str | None) -> str | None:
    """Return ``value`` when it is a well-formed UUID, else ``None``."""
    if not value:
        return None
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return None
