"""Invite-code tools.

``invite.create`` is high risk for an obvious reason: it hands out an account.
The policy engine therefore demands a confirmation, and the service refuses any
role the creator does not outrank.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from meobot.application.audit_service import AuditService
from meobot.application.invite_service import (
    DEFAULT_EXPIRY_DAYS,
    MAX_EXPIRY_DAYS,
    MAX_USES,
    InviteService,
)
from meobot.domain.identity.labels import RoleInput, role_label
from meobot.domain.identity.models import Role
from meobot.domain.permissions.matrix import Permission
from meobot.domain.policy.models import RiskLevel
from meobot.tools.base import NoArguments, ToolContext, ToolDefinition, ToolResult


class CreateInviteArgs(BaseModel):
    """Arguments for ``invite.create``.

    ``role`` is a :data:`~meobot.domain.identity.labels.RoleInput`: the model may
    emit "Member", "trưởng nhóm" or ``TEAM_LEAD`` and every one of them is
    folded to the authoritative enum here, at the edge - so the policy engine
    and :class:`~meobot.application.invite_service.InviteService` only ever see
    a ``Role``, and an alias cannot become a way around either of them.
    """

    model_config = ConfigDict(extra="forbid")

    role: RoleInput = Role.EMPLOYEE
    scope: str | None = Field(default=None, max_length=100)
    note: str | None = Field(default=None, max_length=500)
    expires_in_days: int = Field(default=DEFAULT_EXPIRY_DAYS, ge=1, le=MAX_EXPIRY_DAYS)
    max_uses: int = Field(default=1, ge=1, le=MAX_USES)


async def _list_handler(context: ToolContext, arguments: NoArguments) -> ToolResult:
    session = context.require_session()
    service = InviteService(session, AuditService(session))
    invites = await service.list_invites(active_only=True)
    if not invites:
        return ToolResult(success=True, message="Chưa có mã mời nào đang hoạt động.", data={})
    lines = [
        f"• {role_label(item.role)} · {item.use_count}/{item.max_uses} lượt · "
        f"hết hạn {item.expires_at.date().isoformat() if item.expires_at else 'không'}"
        for item in invites
    ]
    return ToolResult(
        success=True,
        message="🎟 Mã mời đang hoạt động:\n" + "\n".join(lines),
        data={
            "items": [
                {
                    "id": str(item.id),
                    "role": item.role.value,
                    "use_count": item.use_count,
                    "max_uses": item.max_uses,
                    "active": item.active,
                }
                for item in invites
            ]
        },
        entity_type="invite_code",
    )


async def _create_handler(context: ToolContext, arguments: CreateInviteArgs) -> ToolResult:
    session = context.require_session()
    service = InviteService(session, AuditService(session))
    invite, code = await service.create(
        actor=context.actor,
        request_id=context.request_id,
        role=arguments.role,
        scope=arguments.scope,
        note=arguments.note,
        expires_in_days=arguments.expires_in_days,
        max_uses=arguments.max_uses,
    )
    return ToolResult(
        success=True,
        message=(
            f"🎟 Mã mời cho vai trò {role_label(invite.role)}:\n\n`{code}`\n\n"
            f"Dùng được {invite.max_uses} lần, hết hạn "
            f"{invite.expires_at.date().isoformat() if invite.expires_at else 'không'}.\n"
            "Nhân viên gõ: /join <mã>\n"
            "_Mã chỉ hiện một lần; TasksBot chỉ lưu bản băm._"
        ),
        # The plaintext code is never put in `data`: that dict is audited.
        data={
            "invite_id": str(invite.id),
            "role": invite.role.value,
            "role_label": role_label(invite.role),
        },
        entity_type="invite_code",
        entity_id=str(invite.id),
    )


def build_invite_tools() -> list[ToolDefinition]:
    """Invite management tools."""
    return [
        ToolDefinition(
            name="invite.list",
            description="Liệt kê các mã mời đang hoạt động.",
            handler=_list_handler,
            arguments_model=NoArguments,
            risk_level=RiskLevel.LOW,
            required_permission=Permission.USER_MANAGE,
            read_only=True,
        ),
        ToolDefinition(
            name="invite.create",
            description="Tạo mã mời để nhân viên tự đăng ký với TasksBot.",
            handler=_create_handler,
            arguments_model=CreateInviteArgs,
            risk_level=RiskLevel.HIGH,
            # A team lead and above, like ``/create_invite`` and the web panel;
            # the service refuses any role the creator does not outrank.
            min_role=Role.TEAM_LEAD,
            read_only=False,
        ),
    ]
