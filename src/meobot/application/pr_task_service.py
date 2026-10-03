"""PR tasks: creation, who is on them, and the status matrix.

Deliberately **not** coupled to the content workflow. Approving a script does
not finish the task of writing it, and publishing does not finish the task of
seeding. A task is somebody's commitment to do a thing, and the only honest
way to learn it is done is for a person to say so - so nothing here derives a
task status from ``pr_content_items.workflow_stage``, and nothing in the
workflow service touches ``pr_tasks``.

Assignment is a row, not a column - ``pr_task_assignments``, one per person per
role - because a task routinely has an owner, someone helping and someone
reviewing, each accepting and finishing at a different moment.

Unassigning is narrow on purpose. A person who has already **completed** their
part is not removed: their ``completed_at`` is a fact about work that happened,
and deleting the row would erase it to tidy up a list. Removing somebody who
never started is fine, and that is the case the method serves.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.pr_capability_service import PrCapabilityService
from meobot.application.pr_code_service import PrCodeService
from meobot.application.pr_support import lock_row, record_pr_event
from meobot.core.logging import get_logger
from meobot.core.time import utcnow
from meobot.db.models.pr import PrContentItem, PrTask, PrTaskAssignment
from meobot.db.models.user import User
from meobot.domain.audit.models import AuditAction
from meobot.domain.identity.models import Actor
from meobot.domain.pr.errors import (
    PrConflictError,
    PrNotFoundError,
    PrValidationError,
    PrWorkflowTransitionError,
)
from meobot.domain.pr.models import PrPriority, PrTaskAssignmentRole, PrTaskStatus
from meobot.domain.pr.policy import PrCapability
from meobot.domain.pr.workflow import TERMINAL_TASK_STATUSES, assert_task_transition

logger = get_logger(__name__)

MAX_TASK_TITLE_LENGTH = 300
MAX_TASK_TYPE_LENGTH = 50


@dataclass(frozen=True, slots=True)
class CreateTaskCommand:
    """A unit of work, optionally attached to a content item.

    **There is no ``code`` field**: Step 1C.1 allocates ``TSK-YYYY-nnnnnn``
    server-side. See ``docs/pr/STEP_1C1_AUTHORIZATION_AND_CODES.md``.

    ``content_id`` is optional because real PR work includes tasks belonging to
    no single piece: renewing an account, writing a style guide, chasing a
    platform's support desk.
    """

    task_type: str
    title: str
    content_id: uuid.UUID | None = None
    description: str | None = None
    priority: PrPriority = PrPriority.NORMAL
    deadline: datetime | None = None


class PrTaskService:
    """Tasks and their assignees.

    Args:
        session: Unit of work. The caller owns the transaction boundary.
        audit: Event writer sharing that session.
        capabilities: Resolves PR capabilities against roles and grants.
        codes: Allocates ``TSK-YYYY-nnnnnn`` on the same session.
    """

    def __init__(
        self,
        session: AsyncSession,
        audit: AuditService,
        capabilities: PrCapabilityService,
        codes: PrCodeService,
    ) -> None:
        self._session = session
        self._audit = audit
        self._capabilities = capabilities
        self._codes = codes

    # --- Creation ---------------------------------------------------------
    async def create_task(
        self, *, actor: Actor, request_id: uuid.UUID, command: CreateTaskCommand
    ) -> PrTask:
        """Create one task at :attr:`~meobot.domain.pr.models.PrTaskStatus.TODO`."""
        await self._capabilities.require(actor, PrCapability.PR_TASK_MANAGE)

        title = self._require_text(command.title, "title", MAX_TASK_TITLE_LENGTH)
        task_type = self._require_text(command.task_type, "task_type", MAX_TASK_TYPE_LENGTH)
        code = await self._codes.allocate_task_code(at=utcnow())

        if (
            command.content_id is not None
            and await self._session.get(PrContentItem, command.content_id) is None
        ):
            raise PrNotFoundError(
                "No PR content item with that id",
                details={"content_id": str(command.content_id)},
            )

        creator = actor.user_id
        if creator is None:
            raise PrValidationError(
                "A PR task must be created by a known user",
                details={"reason": "actor_has_no_user_row"},
            )

        task = PrTask(
            code=code,
            content_id=command.content_id,
            task_type=task_type,
            title=title,
            description=command.description,
            priority=command.priority,
            status=PrTaskStatus.TODO,
            deadline=command.deadline,
            created_by_user_id=creator,
        )
        self._session.add(task)
        await self._session.flush()

        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_TASK_CREATED,
            entity_type="pr_task",
            entity_id=task.id,
            after={
                "code": task.code,
                "task_type": task.task_type,
                "status": task.status.value,
                "content_id": str(task.content_id) if task.content_id else None,
            },
        )
        logger.info("pr_task_created", extra={"pr_task_id": str(task.id), "code": task.code})
        return task

    # --- Assignment -------------------------------------------------------
    async def assign_user(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        task_id: uuid.UUID,
        user_id: uuid.UUID,
        assignment_role: PrTaskAssignmentRole,
        assigned_at: datetime | None = None,
    ) -> PrTaskAssignment:
        """Attach one person to one task in one capacity.

        Refuses a terminal task: adding an assignee to something already done
        or cancelled records a commitment nobody can act on.

        Raises:
            PrConflictError: That person already holds that role here.
        """
        await self._capabilities.require(actor, PrCapability.PR_TASK_MANAGE)

        task = await self._lock_task(task_id)
        self._refuse_terminal(task, "assign a user to")
        if await self._session.get(User, user_id) is None:
            raise PrNotFoundError("No user with that id", details={"user_id": str(user_id)})

        existing = await self._assignment(task_id, user_id, assignment_role)
        if existing is not None:
            raise PrConflictError(
                "That person already holds that role on this task",
                details={
                    "task_id": str(task_id),
                    "user_id": str(user_id),
                    "assignment_role": assignment_role.value,
                },
            )

        assignment = PrTaskAssignment(
            task_id=task_id,
            user_id=user_id,
            assignment_role=assignment_role,
            assigned_at=assigned_at or utcnow(),
        )
        self._session.add(assignment)
        await self._session.flush()

        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_TASK_ASSIGNED,
            entity_type="pr_task_assignment",
            entity_id=assignment.id,
            after={
                "task_id": str(task_id),
                "task_code": task.code,
                "user_id": str(user_id),
                "assignment_role": assignment_role.value,
            },
        )
        return assignment

    async def unassign_user(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        task_id: uuid.UUID,
        user_id: uuid.UUID,
        assignment_role: PrTaskAssignmentRole,
    ) -> None:
        """Remove somebody who has not completed their part.

        Raises:
            PrNotFoundError: There is no such assignment.
            PrConflictError: They already finished; the row is history now.
        """
        await self._capabilities.require(actor, PrCapability.PR_TASK_MANAGE)

        task = await self._lock_task(task_id)
        assignment = await self._assignment(task_id, user_id, assignment_role)
        if assignment is None:
            raise PrNotFoundError(
                "That person does not hold that role on this task",
                details={
                    "task_id": str(task_id),
                    "user_id": str(user_id),
                    "assignment_role": assignment_role.value,
                },
            )
        if assignment.completed_at is not None:
            raise PrConflictError(
                "That person has already completed their part and cannot be unassigned",
                details={
                    "task_id": str(task_id),
                    "user_id": str(user_id),
                    "assignment_role": assignment_role.value,
                    "completed_at": assignment.completed_at.isoformat(),
                },
            )

        await self._session.delete(assignment)
        await self._session.flush()

        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_TASK_UNASSIGNED,
            entity_type="pr_task",
            entity_id=task_id,
            before={
                "task_code": task.code,
                "user_id": str(user_id),
                "assignment_role": assignment_role.value,
            },
        )

    # --- Status -----------------------------------------------------------
    async def change_status(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        task_id: uuid.UUID,
        target: PrTaskStatus,
        note: str | None = None,
    ) -> PrTask:
        """Move a task along the matrix in :mod:`meobot.domain.pr.workflow`.

        ``completed_at`` is stamped when the task reaches ``DONE`` and is not
        cleared by anything, because nothing can move out of ``DONE``.

        Raises:
            PrWorkflowTransitionError: The transition is not legal.
        """
        await self._capabilities.require(actor, PrCapability.PR_TASK_MANAGE)

        task = await self._lock_task(task_id)
        assert_task_transition(task.status, target)

        before = task.status
        task.status = target
        if target is PrTaskStatus.DONE and task.completed_at is None:
            task.completed_at = utcnow()
        await self._session.flush()

        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_TASK_STATUS_CHANGED,
            entity_type="pr_task",
            entity_id=task.id,
            before={"status": before.value},
            after={"status": target.value, "task_code": task.code, "note": note},
        )
        logger.info(
            "pr_task_status_changed",
            extra={
                "pr_task_id": str(task.id),
                "status_before": before.value,
                "status_after": target.value,
            },
        )
        return task

    # --- Reading ----------------------------------------------------------
    async def require_task(self, task_id: uuid.UUID) -> PrTask:
        task = await self._session.get(PrTask, task_id)
        if task is None:
            raise PrNotFoundError("No PR task with that id", details={"task_id": str(task_id)})
        return task

    async def list_assignments(self, task_id: uuid.UUID) -> Sequence[PrTaskAssignment]:
        result = await self._session.execute(
            select(PrTaskAssignment)
            .where(PrTaskAssignment.task_id == task_id)
            .order_by(PrTaskAssignment.assigned_at.asc())
        )
        return result.scalars().all()

    # --- Internals --------------------------------------------------------
    async def _lock_task(self, task_id: uuid.UUID) -> PrTask:
        task = await lock_row(self._session, PrTask, task_id)
        if task is None:
            raise PrNotFoundError("No PR task with that id", details={"task_id": str(task_id)})
        return task

    async def _assignment(
        self, task_id: uuid.UUID, user_id: uuid.UUID, role: PrTaskAssignmentRole
    ) -> PrTaskAssignment | None:
        result = await self._session.execute(
            select(PrTaskAssignment).where(
                PrTaskAssignment.task_id == task_id,
                PrTaskAssignment.user_id == user_id,
                PrTaskAssignment.assignment_role == role,
            )
        )
        return result.scalars().one_or_none()

    @staticmethod
    def _refuse_terminal(task: PrTask, what: str) -> None:
        if task.status in TERMINAL_TASK_STATUSES:
            raise PrWorkflowTransitionError(
                f"Cannot {what} a task that is {task.status.value}",
                details={"task_id": str(task.id), "current": task.status.value},
            )

    @staticmethod
    def _require_text(value: str, field_name: str, max_length: int) -> str:
        text = (value or "").strip()
        if not text:
            raise PrValidationError(
                f"PR task {field_name} must not be blank", details={"field": field_name}
            )
        if len(text) > max_length:
            raise PrValidationError(
                f"PR task {field_name} is longer than {max_length} characters",
                details={"field": field_name, "max_length": max_length, "length": len(text)},
            )
        return text


__all__: list[str] = ["CreateTaskCommand", "PrTaskService"]
