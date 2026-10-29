"""The employment status ("trạng thái nhân sự") of an account.

Set on the members screen by the OWNER, an ADMIN, or whoever holds the
resources module's ``HR_STATUS`` permission (the Trưởng phòng Tài nguyên by
default). A leaver's resources turn red at once; the recall itself is đợt 2.
A leaver also loses the web app at once: no login (``User.may_sign_in``) and
every open session revoked.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.resources.access import ResourceAccess
from meobot.application.web_auth_service import WebAuthService
from meobot.core.config import Settings
from meobot.core.errors import AuthorizationError, NotFoundError
from meobot.db.models.user import User
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.identity.models import Actor, Role
from meobot.domain.resources.models import EmploymentStatus


class EmploymentStatusForbiddenError(AuthorizationError):
    code = "employment_status_forbidden"

    def __init__(self) -> None:
        super().__init__(
            "Chỉ quản trị viên hoặc Phòng Tài nguyên mới cập nhật được trạng thái nhân sự.",
            details={"reason": "employment_status_forbidden"},
        )


class EmploymentLeftForbiddenError(AuthorizationError):
    code = "employment_status_left_forbidden"

    def __init__(self) -> None:
        super().__init__(
            "Không đặt được chủ sở hữu hoặc chính mình là đã nghỉ việc: "
            "tài khoản sẽ không đăng nhập được nữa.",
            details={"reason": "employment_status_left_forbidden"},
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
    settings: Settings,
) -> User:
    if not access.hr:
        raise EmploymentStatusForbiddenError()
    target = await session.get(User, user_id)
    if target is None or target.deleted_at is not None:
        raise NotFoundError("Không tìm thấy.", details={"reason": "account_not_found"})
    if status is EmploymentStatus.LEFT and (
        target.id == actor.user_id or target.role is Role.OWNER
    ):
        # "Đã nghỉ việc" locks the web app: never the OWNER's, never one's own.
        raise EmploymentLeftForbiddenError()
    before = target.employment_status
    if before is status:
        return target
    target.employment_status = status
    await session.flush()
    if status is EmploymentStatus.LEFT:
        # A leaver may not sign in (``User.may_sign_in``): close every browser
        # session and unused login link now, not when the cookie expires.
        await WebAuthService(session, settings).revoke_all_for_user(user_id=target.id)
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


__all__ = [
    "EmploymentLeftForbiddenError",
    "EmploymentStatusForbiddenError",
    "set_employment_status",
]
