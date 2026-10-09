"""One task, written: an opaque action key, dispatched to the unit's own service.

The keys come from :mod:`.detail_service` and mean nothing to the client. Here
they are parsed back and handed to exactly the write the unit's own route
calls - :class:`OrderCommandService` for Ads, the ``Pr*`` services for PR -
with the same arguments the route would pass. No rule is restated: whether
the move is legal, and whether this person may make it, is decided where it
always was, and refused with the error it always was.

Staleness
---------

Every request carries the ``version`` the screen was drawn from. For an
order it is ``orders.version``, checked by the command service itself
(``order_stale_version``). For PR content it is the number of the draft on
screen, which is what a PR decision binds to (``version_reviewed``); a
newer draft than the one on screen is ``pr_stale_version`` for every PR
action, before anything is written.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from meobot.application.orders.command_service import OrderEdit
from meobot.application.orders.services import OrderServices
from meobot.application.pr_approval_service import RecordApprovalCommand
from meobot.application.pr_production_service import SubmitProductionCommand
from meobot.application.pr_services import PrServices
from meobot.application.tasks.detail_service import TaskDetailService
from meobot.core.errors import ValidationError
from meobot.db.models.task import SOURCE_PR_CONTENT, Task
from meobot.domain.identity.models import Actor
from meobot.domain.orders.pipeline import OrderActionKind
from meobot.domain.pr.errors import (
    PrPermissionDeniedError,
    PrStaleVersionError,
    PrValidationError,
)
from meobot.domain.pr.models import (
    PrApprovalDecision,
    PrPriority,
    PrProductionArtifactType,
    PrWorkflowStage,
)
from meobot.domain.pr.workflow import STAGE_APPROVAL_GATES


class TaskActionKeyError(ValidationError):
    """The key names no action this endpoint knows."""

    code = "task_action_unknown"


@dataclass(frozen=True, slots=True)
class TaskActionCommand:
    key: str
    version: int
    note: str | None = None
    link: str | None = None
    text: str | None = None
    assignee_user_id: uuid.UUID | None = None


def _unknown(key: str) -> TaskActionKeyError:
    return TaskActionKeyError(
        "Thao tác không hợp lệ.", details={"reason": "task_action_unknown", "key": key}
    )


def _blank(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def artifact_type_for(location: str) -> PrProductionArtifactType:
    """How a pasted location is addressed: a Drive link, another link, a NAS path."""
    lowered = location.strip().lower()
    if "drive.google.com" in lowered or "docs.google.com" in lowered:
        return PrProductionArtifactType.DRIVE_LINK
    if lowered.startswith(("http://", "https://")):
        return PrProductionArtifactType.EXTERNAL_LINK
    return PrProductionArtifactType.NAS_PATH


class TaskActionService:
    def __init__(
        self,
        *,
        detail: TaskDetailService,
        pr: PrServices,
        orders: OrderServices,
    ) -> None:
        self._detail = detail
        self._pr = pr
        self._orders = orders

    async def perform(
        self, *, actor: Actor, request_id: uuid.UUID, ref: str, command: TaskActionCommand
    ) -> Task:
        """Run one action. Returns the task, for the caller to re-read."""
        task = await self._detail.resolve(actor, ref)
        parts = command.key.strip().split(":")
        if task.source_type == SOURCE_PR_CONTENT:
            if parts[0] != "pr" or task.pr_content_id is None:
                raise _unknown(command.key)
            await self._pr_action(actor, request_id, task.pr_content_id, parts, command)
        else:
            if parts[0] != "ads" or task.order_id is None:
                raise _unknown(command.key)
            await self._ads_action(actor, request_id, task.order_id, parts, command)
        return task

    # --- Ads ---------------------------------------------------------------------

    async def _ads_action(
        self,
        actor: Actor,
        request_id: uuid.UUID,
        order_id: uuid.UUID,
        parts: list[str],
        command: TaskActionCommand,
    ) -> None:
        if len(parts) not in (2, 3):
            raise _unknown(command.key)
        try:
            kind = OrderActionKind(parts[1])
            node_id = uuid.UUID(parts[2]) if len(parts) == 3 else None
        except ValueError as error:
            raise _unknown(command.key) from error
        commands = self._orders.commands
        version = command.version
        note = _blank(command.note)

        def need_node() -> uuid.UUID:
            if node_id is None:
                raise _unknown(command.key)
            return node_id

        if kind is OrderActionKind.RESUBMIT:
            await commands.resubmit(
                actor=actor,
                request_id=request_id,
                order_id=order_id,
                expected_version=version,
                edit=OrderEdit(),
            )
        elif kind is OrderActionKind.APPROVE_ORDER:
            await commands.approve_order(
                actor=actor, request_id=request_id, order_id=order_id, expected_version=version
            )
        elif kind is OrderActionKind.RETURN_ORDER:
            await commands.return_order(
                actor=actor,
                request_id=request_id,
                order_id=order_id,
                expected_version=version,
                reason=note or "",
            )
        elif kind is OrderActionKind.ASSIGN:
            if command.assignee_user_id is None:
                raise ValidationError(
                    "Chọn người được giao.",
                    details={"reason": "assignee_required", "field": "assignee_user_id"},
                )
            await commands.assign(
                actor=actor,
                request_id=request_id,
                order_id=order_id,
                node_id=need_node(),
                expected_version=version,
                assignee_user_id=command.assignee_user_id,
            )
        elif kind is OrderActionKind.ACCEPT:
            await commands.accept(
                actor=actor,
                request_id=request_id,
                order_id=order_id,
                node_id=need_node(),
                expected_version=version,
            )
        elif kind is OrderActionKind.SUBMIT_WORK:
            await commands.submit_work(
                actor=actor,
                request_id=request_id,
                order_id=order_id,
                node_id=need_node(),
                expected_version=version,
                link=_blank(command.link),
                script_text=_blank(command.text),
                note=note,
            )
        elif kind is OrderActionKind.APPROVE_NODE:
            await commands.approve_node(
                actor=actor,
                request_id=request_id,
                order_id=order_id,
                node_id=need_node(),
                expected_version=version,
                note=note,
            )
        elif kind is OrderActionKind.RETURN_NODE:
            await commands.return_node(
                actor=actor,
                request_id=request_id,
                order_id=order_id,
                node_id=need_node(),
                expected_version=version,
                note=note or "",
            )
        elif kind is OrderActionKind.APPROVE_VIDEO:
            await commands.approve_video(
                actor=actor, request_id=request_id, order_id=order_id, expected_version=version
            )
        elif kind is OrderActionKind.RETURN_VIDEO:
            await commands.return_video(
                actor=actor,
                request_id=request_id,
                order_id=order_id,
                expected_version=version,
                note=note or "",
            )
        elif kind is OrderActionKind.APPROVE_FINAL:
            await commands.approve_final(
                actor=actor,
                request_id=request_id,
                order_id=order_id,
                expected_version=version,
                product_link=_blank(command.link),
            )
        elif kind is OrderActionKind.RETURN_FINAL:
            await commands.return_final(
                actor=actor,
                request_id=request_id,
                order_id=order_id,
                expected_version=version,
                note=note or "",
            )
        elif kind is OrderActionKind.SET_PRIORITY:
            order = await self._orders.queries.resolve(actor, str(order_id))
            await commands.set_priority(
                actor=actor,
                request_id=request_id,
                order_id=order_id,
                expected_version=version,
                is_priority=not order.is_priority,
            )
        elif kind is OrderActionKind.CANCEL:
            await commands.cancel(
                actor=actor,
                request_id=request_id,
                order_id=order_id,
                expected_version=version,
                reason=note or "",
            )
        else:  # pragma: no cover - every kind is listed above
            raise _unknown(command.key)

    # --- PR ------------------------------------------------------------------------

    async def _pr_action(
        self,
        actor: Actor,
        request_id: uuid.UUID,
        content_id: uuid.UUID,
        parts: list[str],
        command: TaskActionCommand,
    ) -> None:
        pr = self._pr
        # The read the PR routes make first: PR's own read permission.
        detail = await pr.queries.get_content(actor=actor, content_id=content_id)
        current = detail.current_version_no or 0
        if command.version != current:
            raise PrStaleVersionError(
                "Nội dung đã được sửa sau khi bạn mở, vui lòng tải lại.",
                details={
                    "reason": "pr_stale_version",
                    "content_id": str(content_id),
                    "expected_version": command.version,
                    "current_version": current,
                },
            )
        kind = parts[1] if len(parts) > 1 else ""
        argument = parts[2] if len(parts) > 2 else None
        if len(parts) > 3:
            raise _unknown(command.key)
        note = _blank(command.note)

        if kind == "TRANSITION" and argument is not None:
            try:
                target = PrWorkflowStage(argument)
            except ValueError as error:
                raise _unknown(command.key) from error
            # The same routing as ``POST /api/pr/contents/{id}/transition``.
            if target is PrWorkflowStage.CANCELLED:
                await pr.workflow.cancel(
                    actor=actor, request_id=request_id, content_id=content_id, note=note
                )
            elif target is PrWorkflowStage.TEAM_LEAD_REVIEW:
                await pr.workflow.submit_to_team_lead_review(
                    actor=actor, request_id=request_id, content_id=content_id, note=note
                )
            else:
                await pr.workflow.request_transition(
                    actor=actor,
                    request_id=request_id,
                    content_id=content_id,
                    target=target,
                    note=note,
                )
        elif kind == "APPROVAL" and argument is not None:
            try:
                decision = PrApprovalDecision(argument)
            except ValueError as error:
                raise _unknown(command.key) from error
            # As ``POST /api/pr/contents/{id}/reviews``: the gate is derived
            # from the stage, the reviewer is the session.
            content = await pr.content.require_content(content_id)
            gate = STAGE_APPROVAL_GATES.get(content.workflow_stage)
            if gate is None:
                raise PrValidationError(
                    "Nội dung này không đang ở bước chờ duyệt.",
                    details={"workflow_stage": content.workflow_stage.value},
                )
            if actor.user_id is None:
                raise PrPermissionDeniedError(
                    "Tài khoản này chưa được liên kết với một người dùng, không thể thực hiện."
                )
            await pr.approvals.record_decision(
                actor=actor,
                request_id=request_id,
                command=RecordApprovalCommand(
                    content_id=content_id,
                    reviewer_user_id=actor.user_id,
                    approval_stage=gate,
                    decision=decision,
                    version_reviewed=command.version,
                    comment=note,
                ),
            )
        elif kind == "SET_PRIORITY" and argument is None:
            priority = self._priority(command.text, detail.content.priority)
            await pr.content.set_content_priority(
                actor=actor, request_id=request_id, content_id=content_id, priority=priority
            )
        elif kind == "ASSIGN_PRODUCER" and argument is None:
            await pr.production.assign_producer(
                actor=actor,
                request_id=request_id,
                content_id=content_id,
                producer_user_id=command.assignee_user_id,
            )
        elif kind == "CLAIM_PRODUCTION" and argument is None:
            await pr.production.claim_production(
                actor=actor, request_id=request_id, content_id=content_id
            )
        elif kind == "START_PRODUCTION" and argument is None:
            await pr.production.start_production(
                actor=actor, request_id=request_id, content_id=content_id
            )
        elif kind == "SUBMIT_PRODUCTION" and argument is None:
            location = command.link or ""
            await pr.production.submit_production(
                actor=actor,
                request_id=request_id,
                command=SubmitProductionCommand(
                    content_id=content_id,
                    artifact_type=artifact_type_for(location),
                    location=location,
                    label=_blank(command.text),
                    note=note,
                ),
            )
        elif kind == "UNDO_LAST_ACTION" and argument is None:
            await pr.undo.undo_last(actor=actor, request_id=request_id, content_id=content_id)
        else:
            raise _unknown(command.key)

    @staticmethod
    def _priority(raw: str | None, current: PrPriority) -> PrPriority:
        """An explicit level when one is sent, else a toggle of "Ưu tiên"."""
        value = _blank(raw)
        if value is not None:
            try:
                return PrPriority(value.upper())
            except ValueError as error:
                raise PrValidationError(
                    f"Mức ưu tiên không hợp lệ: {value!r}.",
                    details={"field": "text", "value": value},
                ) from error
        return PrPriority.NORMAL if current is not PrPriority.NORMAL else PrPriority.HIGH


__all__ = [
    "TaskActionCommand",
    "TaskActionKeyError",
    "TaskActionService",
    "artifact_type_for",
]
