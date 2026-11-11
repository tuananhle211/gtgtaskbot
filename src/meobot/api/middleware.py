"""ASGI middleware: correlation ids, access logging and the hourly timings."""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from meobot.application.ops.perf_stats import PerfCollector
from meobot.core.context import new_request_id, request_context
from meobot.core.logging import get_logger
from meobot.domain.ops.perf_stats import PerfKind

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
            _count(request, duration_ms, response.status_code)
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


def _count(request: Request, duration_ms: float, status_code: int) -> None:
    """Add the request to the hourly timings (plan SCALING_PLAN D), named by
    its route template - never the real path - so no id or query is kept.
    A path no route matched (a scanner, a typo) is not counted."""
    collector = getattr(request.app.state, "perf_collector", None)
    route = request.scope.get("route")
    template = getattr(route, "path", None)
    if not isinstance(collector, PerfCollector) or not isinstance(template, str):
        return
    collector.record(
        PerfKind.HTTP,
        template,
        duration_ms,
        method=request.method,
        failed=status_code >= 500,
    )


def _coerce_uuid(value: str | None) -> str | None:
    """Return ``value`` when it is a well-formed UUID, else ``None``."""
    if not value:
        return None
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return None
