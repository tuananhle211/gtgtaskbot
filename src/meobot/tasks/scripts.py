"""Script review tasks.

Reviews run on ``q_default``: they are LLM calls, not Google calls, and a slow
provider must not block a sheet sync (or vice versa).

A finished review pushes its result to Telegram with the approve / request
revision buttons, which is what makes the Owner's workflow "read the message,
tap a button" rather than "go look for it".
"""

from __future__ import annotations

import uuid
from typing import Any

from celery import Task, shared_task

from meobot.application.audit_service import AuditService
from meobot.application.script_presenter import (
    build_review_keyboard,
    format_review_result,
    short_id,
)
from meobot.application.script_review_service import ScriptReviewService
from meobot.application.script_service import ScriptService
from meobot.application.sheet_profile_service import SheetProfileService
from meobot.application.sheet_writeback_service import SheetWriteBackService
from meobot.core.errors import MeoBotError
from meobot.core.logging import get_logger
from meobot.tasks.celery_app import QUEUE_DEFAULT, QUEUE_INTEGRATIONS, RETRY_KWARGS
from meobot.tasks.runtime import TaskContext, current_request_id, run_async

logger = get_logger(__name__)

#: How many scripts one scheduled sweep reviews. Keeps a backlog from spending
#: an entire LLM budget in one run.
REVIEW_BATCH_SIZE = 5


@shared_task(
    bind=True,
    name="scripts.review_script",
    queue=QUEUE_DEFAULT,
    autoretry_for=(ConnectionError, TimeoutError),
    retry_kwargs=RETRY_KWARGS,
    retry_backoff=True,
    retry_backoff_max=120,
    retry_jitter=True,
)
def review_script(
    self: Task[Any, Any],
    script_id: str,
    *,
    notify_chat_id: int | None = None,
) -> dict[str, Any]:
    """Review one script's current version and report the result.

    Returns a JSON-safe summary. Domain failures are reported to the requester
    and returned as ``{"ok": False}`` rather than raised: a bad script must not
    poison the queue with retries that will fail identically.
    """

    async def work(context: TaskContext) -> dict[str, Any]:
        request_id = current_request_id()
        target = uuid.UUID(script_id)
        chat_id = notify_chat_id or context.owner_chat_id

        try:
            async with context.database.transaction() as session:
                audit = AuditService(session)
                scripts = ScriptService(session, audit)
                await scripts.queue_for_review(script_id=target)
                review = await ScriptReviewService(session, audit, context.llm).review_script(
                    actor=context.system_actor(),
                    request_id=request_id,
                    script_id=target,
                )
                detail = await scripts.detail(target)
                text = format_review_result(detail, review)
                keyboard = build_review_keyboard(
                    target,
                    detail.version_number,
                    secret=context.settings.callback_secret,
                )
                result = {
                    "ok": True,
                    "script_id": script_id,
                    "review_id": str(review.id),
                    "overall_score": review.overall_score,
                    "verdict": review.verdict.value,
                }

                # Write-back happens in the same transaction as the review, so
                # a sheet that says "đã review" is never a sheet without one.
                if detail.profile is not None and detail.version is not None:
                    await SheetWriteBackService(
                        session,
                        audit,
                        context.sheets,
                        timezone_name=context.settings.app_timezone,
                    ).push(
                        actor=context.system_actor(),
                        request_id=request_id,
                        profile=detail.profile,
                        script=detail.script,
                        version=detail.version,
                        review=review,
                    )
        except MeoBotError as exc:
            logger.warning(
                "review_task_failed",
                extra={"script_id": script_id, "error_code": exc.code},
            )
            if chat_id is not None:
                await context.notifier.send(
                    chat_id, f"⛔ Không review được kịch bản `{script_id[:8]}`: {exc.message}"
                )
            return {"ok": False, "script_id": script_id, "error": exc.code}

        if chat_id is not None:
            await context.notifier.send(chat_id, text, reply_markup=keyboard)
        return result

    return run_async(work)


@shared_task(
    bind=True,
    name="scripts.review_pending",
    queue=QUEUE_DEFAULT,
    autoretry_for=(ConnectionError, TimeoutError),
    retry_kwargs=RETRY_KWARGS,
    retry_backoff=True,
    retry_jitter=True,
)
def review_pending(
    self: Task[Any, Any],
    *,
    limit: int = REVIEW_BATCH_SIZE,
    notify_chat_id: int | None = None,
) -> dict[str, Any]:
    """Review the scripts that are waiting for one, oldest first.

    Each script is dispatched as its own ``scripts.review_script`` task so a
    single failure neither retries the whole batch nor loses the rest.
    """

    async def work(context: TaskContext) -> list[str]:
        if not context.settings.auto_review_enabled:
            return []
        async with context.database.session() as session:
            scripts = ScriptService(session, AuditService(session))
            waiting = await scripts.list_awaiting_review(limit=limit)
            return [str(script.id) for script in waiting]

    script_ids = run_async(work)
    for target in script_ids:
        review_script.apply_async(
            args=(target,),
            kwargs={"notify_chat_id": notify_chat_id},
            queue=QUEUE_DEFAULT,
        )
    logger.info("review_pending_dispatched", extra={"count": len(script_ids)})
    return {"dispatched": len(script_ids), "script_ids": script_ids}


@shared_task(
    bind=True,
    name="scripts.push_decision_to_sheet",
    queue=QUEUE_INTEGRATIONS,
    autoretry_for=(ConnectionError, TimeoutError),
    retry_kwargs=RETRY_KWARGS,
    retry_backoff=True,
    retry_jitter=True,
)
def push_decision_to_sheet(
    self: Task[Any, Any],
    script_id: str,
    *,
    approved_by: str | None = None,
    revision_comment: str | None = None,
) -> dict[str, Any]:
    """Write an approval or revision decision back into the source sheet.

    Runs after the decision is committed: a Google failure must never undo a
    human's approval, so this is a separate, retryable unit of work.
    """

    async def work(context: TaskContext) -> dict[str, Any]:
        request_id = current_request_id()
        target = uuid.UUID(script_id)
        async with context.database.transaction() as session:
            audit = AuditService(session)
            scripts = ScriptService(session, audit)
            detail = await scripts.detail(target)
            if detail.profile is None or detail.version is None:
                return {"cells": 0, "reason": "no_sheet_source"}
            profiles = SheetProfileService(session, audit)
            written = await SheetWriteBackService(
                session,
                audit,
                context.sheets,
                timezone_name=context.settings.app_timezone,
            ).push(
                actor=context.system_actor(),
                request_id=request_id,
                profile=await profiles.get(detail.profile.id),
                script=detail.script,
                version=detail.version,
                review=detail.review,
                approved_by=approved_by,
                revision_comment=revision_comment,
            )
        logger.info(
            "sheet_decision_pushed",
            extra={"script_id": short_id(target), "cells": written},
        )
        return {"cells": written}

    return run_async(work)
