"""Google Drive tasks.

These run on ``q_integrations`` so a Drive outage cannot starve reviews or
heartbeats, and because a template copy is slow enough that doing it inside a
Telegram handler would visibly hang the bot.

All of them are safe to retry. The creation tasks derive an idempotency key
from the confirmation that triggered them, so a Celery retry finds the existing
``created_spreadsheets`` row - and, if Drive already made the file, reconciles
to it rather than creating a second one.
"""

from __future__ import annotations

import uuid
from typing import Any

from celery import Task, shared_task

from meobot.application.audit_service import AuditService
from meobot.application.drive_folder_service import DriveFolderService
from meobot.application.spreadsheet_creation_service import SpreadsheetCreationService
from meobot.core.errors import MeoBotError
from meobot.core.logging import get_logger
from meobot.domain.drive.models import SpreadsheetRequest, TemplateKind
from meobot.domain.drive.templates import template_for_kind
from meobot.domain.identity.models import Actor, Role
from meobot.tasks.celery_app import QUEUE_INTEGRATIONS, RETRY_KWARGS
from meobot.tasks.runtime import TaskContext, current_request_id, run_async

logger = get_logger(__name__)


def _actor_from(payload: dict[str, Any], context: TaskContext) -> Actor:
    """Rebuild the requesting actor from the task payload.

    The role travels with the task rather than being re-derived, so a task
    cannot execute with more authority than the person who confirmed it. It is
    still re-checked against the folder by ``assert_usable_by``.
    """
    raw_user_id = payload.get("actor_user_id")
    try:
        user_id = uuid.UUID(str(raw_user_id)) if raw_user_id else None
    except ValueError:  # pragma: no cover - malformed payload
        user_id = None
    try:
        role = Role(str(payload.get("actor_role") or Role.EMPLOYEE.value))
    except ValueError:  # pragma: no cover - malformed payload
        role = Role.EMPLOYEE
    telegram_id = payload.get("actor_telegram_id")
    return Actor(
        user_id=user_id,
        telegram_user_id=int(telegram_id) if telegram_id is not None else None,
        telegram_username=None,
        full_name=str(payload.get("actor_name") or "telegram-user"),
        role=role,
        active=True,
        is_bootstrap_owner=(
            telegram_id is not None
            and context.settings.meobot_owner_telegram_id == int(telegram_id)
        ),
    )


def _request_id_from(payload: dict[str, Any]) -> uuid.UUID:
    """Reuse the Telegram correlation id when the payload carried one."""
    raw = payload.get("request_id")
    try:
        return uuid.UUID(str(raw)) if raw else current_request_id()
    except ValueError:  # pragma: no cover - malformed payload
        return current_request_id()


@shared_task(
    bind=True,
    name="drive.validate_folder",
    queue=QUEUE_INTEGRATIONS,
    autoretry_for=(ConnectionError, TimeoutError),
    retry_kwargs=RETRY_KWARGS,
    retry_backoff=True,
    retry_jitter=True,
)
def validate_folder(
    self: Task[Any, Any],
    folder_id: str,
    *,
    notify_chat_id: int | None = None,
) -> dict[str, Any]:
    """Re-check one registered folder against Google.

    Args:
        folder_id: UUID of the ``drive_folders`` row.
        notify_chat_id: Telegram chat that asked, if any.
    """

    async def work(context: TaskContext) -> dict[str, Any]:
        request_id = current_request_id()
        actor = context.system_actor()
        async with context.database.transaction() as session:
            service = DriveFolderService(
                session, AuditService(session), context.drive, context.settings
            )
            folder = await service.revalidate(
                actor=actor, request_id=request_id, folder_id=uuid.UUID(folder_id)
            )
            result = {
                "id": str(folder.id),
                "name": folder.name,
                "validation_status": folder.validation_status,
                "error": folder.validation_error,
            }

        if notify_chat_id is not None:
            status = result["validation_status"]
            mark = "✅" if status == "valid" else "⚠️"
            await context.notifier.send(
                notify_chat_id,
                f"{mark} Thư mục {result['name']}: {status}"
                + (f"\n{result['error']}" if result["error"] else ""),
            )
        return result

    return run_async(work)


def _create_spreadsheet(kind: TemplateKind, payload: dict[str, Any]) -> dict[str, Any]:
    """Shared body of the two creation tasks."""

    async def work(context: TaskContext) -> dict[str, Any]:
        request_id = _request_id_from(payload)
        actor = _actor_from(payload, context)
        spec = template_for_kind(kind)
        notify_chat_id = payload.get("notify_chat_id")

        request = SpreadsheetRequest(
            template_code=spec.code,
            template_version=spec.version,
            name=str(payload.get("name") or "Sheet"),
            folder_id=str(payload.get("folder_drive_id") or ""),
            kind=kind,
            team=payload.get("team"),
            channel=payload.get("channel"),
            campaign=payload.get("campaign"),
            period=payload.get("period"),
            actor_reference=str(actor.telegram_user_id or actor.user_id or ""),
            confirmation_reference=str(payload.get("confirmation_reference") or ""),
            register_profile=kind is TemplateKind.SCRIPT_MANAGEMENT,
            run_initial_sync=kind is TemplateKind.SCRIPT_MANAGEMENT,
        )

        try:
            async with context.database.transaction() as session:
                service = SpreadsheetCreationService(
                    session,
                    AuditService(session),
                    context.drive,
                    context.sheets,
                    context.settings,
                )
                outcome = await service.create(actor=actor, request_id=request_id, request=request)
                result = {
                    "id": str(outcome.record.id),
                    "name": outcome.record.name,
                    "kind": outcome.record.kind,
                    "spreadsheet_id": outcome.record.spreadsheet_id,
                    "spreadsheet_url": outcome.record.spreadsheet_url,
                    "creation_method": outcome.record.creation_method,
                    "created_now": outcome.created_now,
                    "sheet_profile_id": (str(outcome.profile_id) if outcome.profile_id else None),
                }
        except MeoBotError as exc:
            logger.warning(
                "drive_creation_failed",
                extra={"kind": kind.value, "error_code": exc.code},
            )
            if notify_chat_id is not None:
                await context.notifier.send(
                    int(notify_chat_id),
                    f"⛔ Không tạo được Sheet {request.name!r}.\n{exc.message}",
                )
            raise

        if notify_chat_id is not None:
            await context.notifier.send(int(notify_chat_id), _success_message(result, spec))

        if request.run_initial_sync and result["sheet_profile_id"]:
            from meobot.tasks.sheets import sync_profile

            sync_profile.apply_async(
                args=(result["sheet_profile_id"],),
                kwargs={"notify_chat_id": notify_chat_id},
            )
        return result

    return run_async(work)


def _success_message(result: dict[str, Any], spec: Any) -> str:
    """What the requester is told when the file exists.

    Says whether it was created *now* or found from an earlier attempt, so a
    duplicate press never reads as "created twice".
    """
    method = (
        "sao chép từ mẫu có sẵn định dạng"
        if result.get("creation_method") == "template_copy"
        else "Sheet trắng theo chuẩn"
    )
    head = "✅ Đã tạo Sheet" if result.get("created_now") else "♻️ Sheet này đã được tạo trước đó"
    lines = [
        f"{head}: {result['name']}",
        f"Cách tạo: {method}",
        f"Tab: {', '.join(spec.expected_tabs)}",
    ]
    if result.get("spreadsheet_url"):
        lines.append(result["spreadsheet_url"])
    if result.get("sheet_profile_id"):
        lines.append(f"Sheet Profile: {result['sheet_profile_id']}")
        lines.append("Đang đồng bộ lần đầu. Xem thêm: /sheets · /pending_scripts")
    else:
        lines.append("Xem lại sau bằng /created_sheets")
    return "\n".join(lines)


@shared_task(
    bind=True,
    name="drive.create_work_spreadsheet",
    queue=QUEUE_INTEGRATIONS,
    autoretry_for=(ConnectionError, TimeoutError),
    retry_kwargs=RETRY_KWARGS,
    retry_backoff=True,
    retry_backoff_max=120,
    retry_jitter=True,
)
def create_work_spreadsheet(
    self: Task[Any, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Create a work-management spreadsheet.

    Never registers a Sheet Profile: a task board is not a script source.

    Args:
        payload: The confirmed request - name, destination folder, actor and
            the confirmation reference the idempotency key is derived from.
    """
    return _create_spreadsheet(TemplateKind.WORK_MANAGEMENT, payload)


@shared_task(
    bind=True,
    name="drive.create_script_spreadsheet",
    queue=QUEUE_INTEGRATIONS,
    autoretry_for=(ConnectionError, TimeoutError),
    retry_kwargs=RETRY_KWARGS,
    retry_backoff=True,
    retry_backoff_max=120,
    retry_jitter=True,
)
def create_script_spreadsheet(
    self: Task[Any, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Create a script-management spreadsheet and register its Sheet Profile.

    The mapping comes from the template definition, not from the LLM: MeoBot
    designed this layout, so guessing at it would be strictly worse.

    Args:
        payload: The confirmed request - see :func:`create_work_spreadsheet`.
    """
    return _create_spreadsheet(TemplateKind.SCRIPT_MANAGEMENT, payload)


@shared_task(
    bind=True,
    name="drive.reconcile_created_spreadsheets",
    queue=QUEUE_INTEGRATIONS,
    autoretry_for=(ConnectionError, TimeoutError),
    retry_kwargs=RETRY_KWARGS,
    retry_backoff=True,
    retry_jitter=True,
)
def reconcile_created_spreadsheets(self: Task[Any, Any]) -> dict[str, int]:
    """Settle creation records stuck in ``pending``.

    A task that died between "Drive said yes" and "the transaction committed"
    leaves a pending row and a real file. This finds the file by MeoBot's own
    ``meobot_idempotency_reference`` property and settles the row. It never
    creates anything.
    """

    async def work(context: TaskContext) -> dict[str, int]:
        request_id = current_request_id()
        actor = context.system_actor()
        async with context.database.transaction() as session:
            service = SpreadsheetCreationService(
                session,
                AuditService(session),
                context.drive,
                context.sheets,
                context.settings,
            )
            return await service.reconcile_pending(actor=actor, request_id=request_id)

    return run_async(work)
