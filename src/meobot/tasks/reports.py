"""Report tasks.

Milestone 1 ships one demo task that exercises the ``q_reports`` queue and the
retry policy without touching Google, Meta or TikTok.
"""

from __future__ import annotations

from typing import Any

from celery import Task, shared_task

from meobot.core.context import get_request_id
from meobot.core.logging import get_logger
from meobot.core.time import utcnow
from meobot.tasks.celery_app import RETRY_KWARGS

logger = get_logger(__name__)


@shared_task(
    bind=True,
    name="reports.generate_demo",
    autoretry_for=(ConnectionError, TimeoutError),
    retry_kwargs=RETRY_KWARGS,
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
)
def generate_demo(self: Task[Any, Any], period: str = "daily") -> dict[str, Any]:
    """Produce a fake report payload.

    Args:
        period: ``daily``, ``weekly`` or ``monthly``. Validated here rather than
            trusted, because a task name plus arguments is an execution surface.

    Returns:
        A dict describing the report that *would* be produced.

    Raises:
        ValueError: When ``period`` is not a supported value. Not retried - a
            bad argument will still be bad on the next attempt.

    TODO(milestone-3): replace with a real report built from production data and
    written to Google Drive via ``DriveClient``.
    """
    allowed = {"daily", "weekly", "monthly"}
    if period not in allowed:
        raise ValueError(f"Unsupported report period {period!r}; expected one of {sorted(allowed)}")

    logger.info(
        "report_demo_generated",
        extra={"task": "reports.generate_demo", "period": period, "attempt": self.request.retries},
    )
    return {
        "period": period,
        "generated_at": utcnow().isoformat(),
        "request_id": get_request_id(),
        "rows": 0,
        "note": "Demo report - no real data source is wired yet.",
    }
