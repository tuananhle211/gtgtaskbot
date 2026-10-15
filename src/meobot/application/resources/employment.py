"""The employment status ("trạng thái nhân sự") of an account.

Set on the members screen by the OWNER, an ADMIN, or whoever holds the
resources module's ``HR_STATUS`` permission (the Trưởng phòng Tài nguyên by
default). A leaver's resources turn red at once; the recall itself is đợt 2.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.resources.access import ResourceAccess
from meobot.core.errors import AuthorizationError, NotFoundError
from meobot.db.models.user import User
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.identity.models import Actor
from meobot.domain.resources.models import EmploymentStatus


class EmploymentStatusForbiddenError(AuthorizationError):
    code = "employment_status_forbidden"

    def __init__(self) -> None:
        super().__init__(
            "Chỉ quản trị viên hoặc Phòng Tài nguyên mới cập nhật được trạng thái nhân sự.",
            details={"reason": "employment_status_forbidden"},
        )


async def set_employment_status(
    session: AsyncSession,
    audit: AuditService,
    *,
    actor: Actor,
    access: ResourceAccess,
    request_id: uuid.UUID,
    user_id: uuid.UUID,
    status: EmploymentStatus,
) -> User:
    if not access.hr:
        raise EmploymentStatusForbiddenError()
    target = await session.get(User, user_id)
    if target is None or target.deleted_at is not None:
        raise NotFoundError("Không tìm thấy.", details={"reason": "account_not_found"})
    before = target.employment_status
    if before is status:
        return target
    target.employment_status = status
    await session.flush()
    await audit.record_action(
        request_id=request_id,
        actor=actor,
        action=AuditAction.USER_EMPLOYMENT_STATUS_UPDATED.value,
        result=AuditResult.SUCCESS,
        entity_type="user",
        entity_id=str(target.id),
        before_data={"employment_status": before.value},
        after_data={"employment_status": status.value},
    )
    return target


__all__ = ["EmploymentStatusForbiddenError", "set_employment_status"]
