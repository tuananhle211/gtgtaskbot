"""Telegram tools over PR tasks.

The status tools are deliberately thin: each names one target status and hands
it to
:meth:`~meobot.application.pr_task_service.PrTaskService.change_status`, which
consults the matrix in :mod:`meobot.domain.pr.workflow`. **The matrix is not
repeated here.** A tool that pre-checked "can this go from TODO to DONE" would
be a second copy of a table that already exists, and the copy would be the one
that went stale.

Separate tools per transition rather than one tool with a ``status`` argument,
because that is what makes "đánh dấu TSK-… đang làm" routable: the model picks
a verb, not an enum value. The invalid ones still fail - in the service, with a
message naming what *was* possible.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from meobot.application.pr_task_service import CreateTaskCommand
from meobot.core.errors import ToolExecutionError
from meobot.domain.permissions.matrix import Permission
from meobot.domain.policy.models import RiskLevel
from meobot.domain.pr.models import PrPriority, PrTaskAssignmentRole, PrTaskStatus
from meobot.tools.base import ToolContext, ToolDefinition, ToolResult
from meobot.tools.pr_errors import pr_errors
from meobot.tools.pr_presenters import format_task_list, format_task_summary, task_status_label
from meobot.tools.pr_support import (
    person_names,
    pr_services,
    resolve_content,
    resolve_deadline,
    resolve_person,
    timezone_of,
)


class CreateTaskArgs(BaseModel):
    """Arguments for ``pr.task.create``. No ``code``: it is generated."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=300)
    task_type: str = Field(
        default="GENERAL", max_length=50, description="Loại việc, ví dụ SCRIPT, EDIT, SEEDING."
    )
    content: str | None = Field(
        default=None, max_length=300, description="Nội dung liên quan, nếu có."
    )
    description: str | None = Field(default=None, max_length=4000)
    deadline: str | None = Field(default=None, description="Hạn, ví dụ 'thứ Sáu' hoặc '31/8'.")
    priority: str | None = Field(
        default=None, description="NORMAL, HIGH, URGENT hoặc CRITICAL. Mặc định NORMAL."
    )
    assignee: str | None = Field(default=None, max_length=200, description="Tên người làm.")


class TaskReferenceArgs(BaseModel):
    """Arguments for tools addressing one task."""

    model_config = ConfigDict(extra="forbid")

    task: str = Field(min_length=1, max_length=100, description="Mã task (TSK-…).")
    note: str | None = Field(default=None, max_length=1000)


class AssignTaskArgs(BaseModel):
    """Arguments for ``pr.task.assign``."""

    model_config = ConfigDict(extra="forbid")

    task: str = Field(min_length=1, max_length=100)
    assignee: str = Field(min_length=1, max_length=200, description="Tên người nhận việc.")
    role: str = Field(default="OWNER", description="OWNER, CONTRIBUTOR hoặc REVIEWER.")


class ListTasksArgs(BaseModel):
    """Arguments for ``pr.task.list``."""

    model_config = ConfigDict(extra="forbid")

    content: str | None = Field(default=None, max_length=300)
    status: str | None = Field(default=None, description="Lọc theo trạng thái.")
    open_only: bool = Field(default=False, description="Chỉ lấy task chưa xong/chưa huỷ.")
    limit: int = Field(default=10, ge=1, le=25)


class OverdueArgs(BaseModel):
    """Arguments for ``pr.task.overdue``."""

    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=15, ge=1, le=50)


def _priority(raw: str | None) -> PrPriority:
    if not raw:
        return PrPriority.NORMAL
    try:
        return PrPriority(raw.strip().upper())
    except ValueError:
        return PrPriority.NORMAL


def _assignment_role(raw: str) -> PrTaskAssignmentRole:
    try:
        return PrTaskAssignmentRole(raw.strip().upper())
    except ValueError:
        return PrTaskAssignmentRole.OWNER


async def _create_handler(context: ToolContext, arguments: CreateTaskArgs) -> ToolResult:
    with pr_errors():
        services = pr_services(context)
        content_id = None
        if arguments.content:
            content_id = (
                await resolve_content(services, actor=context.actor, reference=arguments.content)
            ).id

        deadline: datetime | None = await resolve_deadline(
            services, text=arguments.deadline, context=context
        )
        task = await services.tasks.create_task(
            actor=context.actor,
            request_id=context.request_id,
            command=CreateTaskCommand(
                task_type=arguments.task_type.strip().upper() or "GENERAL",
                title=arguments.title,
                content_id=content_id,
                description=arguments.description,
                priority=_priority(arguments.priority),
                deadline=deadline,
            ),
        )

        assigned_to: str | None = None
        if arguments.assignee:
            user_id = await resolve_person(services, name=arguments.assignee)
            await services.tasks.assign_user(
                actor=context.actor,
                request_id=context.request_id,
                task_id=task.id,
                user_id=user_id,
                assignment_role=PrTaskAssignmentRole.OWNER,
            )
            assigned_to = (await person_names(services, [user_id])).get(str(user_id))

        summary = format_task_summary(
            task,
            tz=timezone_of(context),
            assignee_names=[assigned_to] if assigned_to else [],
        )
        return ToolResult(
            success=True,
            message=f"✅ Đã tạo task.\n\n{summary}",
            data={
                "task_id": str(task.id),
                "code": task.code,
                "status": task.status.value,
                "assigned_to": assigned_to,
            },
            entity_type="pr_task",
            entity_id=task.code,
        )


async def _get_handler(context: ToolContext, arguments: TaskReferenceArgs) -> ToolResult:
    with pr_errors():
        services = pr_services(context)
        task = await services.queries.get_task_by_code(actor=context.actor, code=arguments.task)
        assignments = list(await services.tasks.list_assignments(task.id))
        names = await person_names(services, [row.user_id for row in assignments])
        return ToolResult(
            success=True,
            message=format_task_summary(
                task,
                tz=timezone_of(context),
                assignee_names=[names.get(str(row.user_id), "—") for row in assignments],
            ),
            data={
                "code": task.code,
                "status": task.status.value,
                "assignee_count": len(assignments),
            },
            entity_type="pr_task",
            entity_id=task.code,
        )


async def _list_handler(context: ToolContext, arguments: ListTasksArgs) -> ToolResult:
    with pr_errors():
        services = pr_services(context)
        content_id = None
        if arguments.content:
            content_id = (
                await resolve_content(services, actor=context.actor, reference=arguments.content)
            ).id
        status = None
        if arguments.status:
            try:
                status = PrTaskStatus(arguments.status.strip().upper())
            except ValueError:
                raise ToolExecutionError(
                    f'Mình chưa hiểu trạng thái "{arguments.status}".',
                    details={"reason": "unknown_task_status"},
                ) from None

        tasks = list(
            await services.queries.list_tasks(
                actor=context.actor,
                content_id=content_id,
                status=status,
                open_only=arguments.open_only,
                limit=arguments.limit,
            )
        )
        return ToolResult(
            success=True,
            message=format_task_list(
                tasks,
                tz=timezone_of(context),
                heading="🧩 <b>Task</b>",
                empty="Không có task nào khớp bộ lọc.",
            ),
            data={"count": len(tasks), "codes": [task.code for task in tasks]},
            entity_type="pr_task",
        )


async def _overdue_handler(context: ToolContext, arguments: OverdueArgs) -> ToolResult:
    with pr_errors():
        services = pr_services(context)
        tasks = list(
            await services.queries.list_overdue_tasks(actor=context.actor, limit=arguments.limit)
        )
        return ToolResult(
            success=True,
            message=format_task_list(
                tasks,
                tz=timezone_of(context),
                heading="⏰ <b>Task quá hạn</b>",
                empty="Không có task nào quá hạn.",
            ),
            data={"count": len(tasks), "codes": [task.code for task in tasks]},
            entity_type="pr_task",
        )


async def _assign_handler(context: ToolContext, arguments: AssignTaskArgs) -> ToolResult:
    with pr_errors():
        services = pr_services(context)
        task = await services.queries.get_task_by_code(actor=context.actor, code=arguments.task)
        user_id = await resolve_person(services, name=arguments.assignee)
        await services.tasks.assign_user(
            actor=context.actor,
            request_id=context.request_id,
            task_id=task.id,
            user_id=user_id,
            assignment_role=_assignment_role(arguments.role),
        )
        who = (await person_names(services, [user_id])).get(str(user_id), arguments.assignee)
        return ToolResult(
            success=True,
            message=f"👤 Đã giao <b>{task.code}</b> cho {who}.",
            data={"code": task.code, "user_id": str(user_id)},
            entity_type="pr_task",
            entity_id=task.code,
        )


def _status_handler(target: PrTaskStatus, verb: str):  # type: ignore[no-untyped-def]
    """One handler per transition, all landing in the same service call."""

    async def handler(context: ToolContext, arguments: TaskReferenceArgs) -> ToolResult:
        with pr_errors():
            services = pr_services(context)
            task = await services.queries.get_task_by_code(actor=context.actor, code=arguments.task)
            updated = await services.tasks.change_status(
                actor=context.actor,
                request_id=context.request_id,
                task_id=task.id,
                target=target,
                note=arguments.note,
            )
            return ToolResult(
                success=True,
                message=f"{verb} <b>{task.code}</b> → {task_status_label(updated.status)}.",
                data={"code": task.code, "status": updated.status.value},
                entity_type="pr_task",
                entity_id=task.code,
            )

    return handler


def build_pr_task_tools() -> list[ToolDefinition]:
    """Task tools. Cancellation is HIGH risk, so it inherits ``/confirm``.

    None is ``destructive``: that flag means "deletes data" here and
    ``PolicyEngine`` refuses such tools outright. Cancelling a task is a status
    change, and the task stays.
    """
    transitions: list[tuple[str, str, PrTaskStatus, str, RiskLevel, bool]] = [
        (
            "pr.task.start",
            "Đánh dấu một task PR là đang làm.",
            PrTaskStatus.IN_PROGRESS,
            "▶️",
            RiskLevel.LOW,
            False,
        ),
        (
            "pr.task.block",
            "Đánh dấu một task PR đang bị vướng.",
            PrTaskStatus.BLOCKED,
            "⛔",
            RiskLevel.LOW,
            False,
        ),
        (
            "pr.task.submit_review",
            "Chuyển một task PR sang chờ duyệt.",
            PrTaskStatus.IN_REVIEW,
            "📤",
            RiskLevel.LOW,
            False,
        ),
        (
            "pr.task.request_revision",
            "Trả một task PR về để sửa lại.",
            PrTaskStatus.REVISION_REQUIRED,
            "↩️",
            RiskLevel.MEDIUM,
            False,
        ),
        (
            "pr.task.complete",
            "Đánh dấu một task PR đã xong.",
            PrTaskStatus.DONE,
            "✅",
            RiskLevel.MEDIUM,
            False,
        ),
        ("pr.task.cancel", "Huỷ một task PR.", PrTaskStatus.CANCELLED, "🗑", RiskLevel.HIGH, False),
    ]
    tools = [
        ToolDefinition(
            name="pr.task.create",
            description=(
                "Tạo một task PR. Mã task (TSK-…) do hệ thống tự sinh. "
                "Có thể giao luôn cho một người và đặt hạn bằng ngôn ngữ tự nhiên."
            ),
            handler=_create_handler,
            arguments_model=CreateTaskArgs,
            risk_level=RiskLevel.MEDIUM,
            required_permission=Permission.SCRIPT_SUBMIT,
            read_only=False,
        ),
        ToolDefinition(
            name="pr.task.get",
            description="Xem chi tiết một task PR.",
            handler=_get_handler,
            arguments_model=TaskReferenceArgs,
            risk_level=RiskLevel.LOW,
            required_permission=Permission.SCRIPT_READ,
            read_only=True,
        ),
        ToolDefinition(
            name="pr.task.list",
            description="Liệt kê task PR theo nội dung hoặc trạng thái.",
            handler=_list_handler,
            arguments_model=ListTasksArgs,
            risk_level=RiskLevel.LOW,
            required_permission=Permission.SCRIPT_READ,
            read_only=True,
        ),
        ToolDefinition(
            name="pr.task.overdue",
            description="Liệt kê task PR đã quá hạn mà chưa xong.",
            handler=_overdue_handler,
            arguments_model=OverdueArgs,
            risk_level=RiskLevel.LOW,
            required_permission=Permission.SCRIPT_READ,
            read_only=True,
        ),
        ToolDefinition(
            name="pr.task.assign",
            description="Giao một task PR cho một người trong TasksBot.",
            handler=_assign_handler,
            arguments_model=AssignTaskArgs,
            risk_level=RiskLevel.MEDIUM,
            required_permission=Permission.SCRIPT_SUBMIT,
            read_only=False,
        ),
    ]
    tools.extend(
        ToolDefinition(
            name=name,
            description=description,
            handler=_status_handler(status, verb),
            arguments_model=TaskReferenceArgs,
            risk_level=risk,
            required_permission=Permission.SCRIPT_SUBMIT,
            destructive=destructive,
            read_only=False,
        )
        for name, description, status, verb, risk, destructive in transitions
    )
    return tools


__all__ = ["build_pr_task_tools"]
