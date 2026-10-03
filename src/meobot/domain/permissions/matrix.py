"""Static role -> permission matrix.

Kept as data (not code branches) so it can later be moved into
``system_settings`` and edited conversationally without touching the engine.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType

from meobot.domain.identity.models import Role


class Permission(StrEnum):
    """Atomic capability checked by the policy engine."""

    # System
    SYSTEM_HEALTH = "system.health"
    SYSTEM_INFO = "system.info"
    SETTINGS_READ = "settings.read"
    SETTINGS_WRITE = "settings.write"
    USER_MANAGE = "user.manage"
    DATA_DELETE = "data.delete"

    # Conversation
    CONVERSATION_USE = "conversation.use"

    # Access control: who MeoBot answers, where, and how often.
    #
    # These are new in 0.5.0 and are deliberately OWNER-only, apart from the
    # two read permissions. Handing out access is the one operation that can
    # widen every other permission in this table, so it stays with the person
    # the deployment is configured for. ``USER_MANAGE`` (and with it the
    # existing ADMIN invitation flow) is untouched.
    GROUP_MEMBER_POLICY_READ = "group_member_policy.read"
    GROUP_MEMBER_POLICY_MANAGE = "group_member_policy.manage"
    GUEST_ACCESS_MANAGE = "guest_access.manage"
    USER_READ = "user.read"
    USER_ADD_DIRECT = "user.add_direct"
    USER_STATUS_MANAGE = "user.status.manage"
    USER_ROLE_MANAGE = "user.role.manage"
    USER_QUOTA_MANAGE = "user.quota.manage"

    # Configuration of content pipeline
    SCRIPT_TYPE_READ = "script_type.read"
    SCRIPT_TYPE_WRITE = "script_type.write"
    SHEET_PROFILE_READ = "sheet_profile.read"
    SHEET_PROFILE_WRITE = "sheet_profile.write"

    # Google Drive & spreadsheet creation
    DRIVE_FOLDER_READ = "drive.folder.read"
    DRIVE_FOLDER_MANAGE = "drive.folder.manage"
    SHEET_TEMPLATE_READ = "sheet_template.read"
    SHEET_TEMPLATE_MANAGE = "sheet_template.manage"
    SPREADSHEET_CREATE = "spreadsheet.create"
    SPREADSHEET_READ = "spreadsheet.read"

    # Scripts
    SCRIPT_READ = "script.read"
    SCRIPT_SUBMIT = "script.submit"
    SCRIPT_REVIEW = "script.review"
    SCRIPT_APPROVE = "script.approve"

    # Video production & publishing
    VIDEO_READ = "video.read"
    VIDEO_SUBMIT = "video.submit"
    VIDEO_APPROVE = "video.approve"
    PUBLISH_SOCIAL = "publish.social"

    # Analytics & reporting
    METRICS_READ = "metrics.read"
    REPORT_GENERATE = "report.generate"

    # HR
    HR_REQUEST_LEAVE = "hr.request_leave"
    #: Legacy, held by TEAM_LEAD since 0.1.0 and deliberately left alone. It is
    #: *not* what gates the 0.6.0A approval flow - see ``HR_REQUEST_APPROVE``.
    HR_APPROVE_LEAVE = "hr.approve_leave"
    HR_REPORT_READ = "hr.report_read"
    #: Deciding leave and late-arrival requests. OWNER-only in this release;
    #: delegating it is a future decision, not a side effect of reusing the
    #: older permission above.
    HR_REQUEST_APPROVE = "hr.request_approve"
    #: Department-wide HR figures. OWNER-only: a Member must never be able to
    #: read another person's absence history.
    HR_DEPARTMENT_REPORT = "hr.department_report"
    #: Setting working hours and company holidays.
    HR_SCHEDULE_MANAGE = "hr.schedule_manage"

    # Cross-chat notification (0.6.0a1)
    #: Speaking to a whole group in MeoBot's voice. Deliberately *not*
    #: ``settings.write``: registering a destination is configuration, but
    #: broadcasting to it is speech, and only the owner does that by default.
    ANNOUNCEMENT_BROADCAST = "announcement.broadcast"
    #: Reading delivery status and retrying failed messages.
    NOTIFICATION_MANAGE = "notification.manage"

    # Reminders
    REMINDER_MANAGE = "reminder.manage"


_EMPLOYEE: frozenset[Permission] = frozenset(
    {
        Permission.SYSTEM_HEALTH,
        Permission.CONVERSATION_USE,
        Permission.SCRIPT_TYPE_READ,
        Permission.SCRIPT_READ,
        Permission.SCRIPT_SUBMIT,
        Permission.SPREADSHEET_READ,
        Permission.VIDEO_READ,
        Permission.VIDEO_SUBMIT,
        Permission.HR_REQUEST_LEAVE,
        Permission.REMINDER_MANAGE,
    }
)

#: Team leads may create Sheets, but only in folders with no team restriction -
#: see ``DriveFolderService.assert_usable_by``. MeoBot does not model which
#: team a person belongs to, so a team-scoped folder is admin-only for now.
_TEAM_LEAD: frozenset[Permission] = _EMPLOYEE | frozenset(
    {
        Permission.SYSTEM_INFO,
        Permission.SHEET_PROFILE_READ,
        Permission.SCRIPT_REVIEW,
        Permission.SCRIPT_APPROVE,
        Permission.VIDEO_APPROVE,
        Permission.METRICS_READ,
        Permission.REPORT_GENERATE,
        Permission.HR_APPROVE_LEAVE,
        Permission.HR_REPORT_READ,
        Permission.DRIVE_FOLDER_READ,
        Permission.SHEET_TEMPLATE_READ,
        Permission.SPREADSHEET_CREATE,
    }
)

#: The admin keeps every business permission they had, including
#: ``USER_MANAGE`` and therefore invite codes. What they do *not* get in this
#: release is the access-control set: granting Guest access, changing an
#: account's status or role, and overriding somebody's quota are all decisions
#: about who may use the system at all, and they stay with the owner.
_ADMIN: frozenset[Permission] = _TEAM_LEAD | frozenset(
    {
        Permission.SETTINGS_READ,
        Permission.SETTINGS_WRITE,
        Permission.SCRIPT_TYPE_WRITE,
        Permission.SHEET_PROFILE_WRITE,
        Permission.PUBLISH_SOCIAL,
        Permission.USER_MANAGE,
        Permission.USER_READ,
        Permission.GROUP_MEMBER_POLICY_READ,
        Permission.DRIVE_FOLDER_MANAGE,
        Permission.SHEET_TEMPLATE_MANAGE,
    }
)

#: The owner holds every permission, including the ones the policy engine
#: still refuses to execute in v1 (see ``DENY_IN_V1``).
_OWNER: frozenset[Permission] = frozenset(Permission)

ROLE_PERMISSIONS: Mapping[Role, frozenset[Permission]] = MappingProxyType(
    {
        Role.EMPLOYEE: _EMPLOYEE,
        Role.TEAM_LEAD: _TEAM_LEAD,
        Role.ADMIN: _ADMIN,
        Role.OWNER: _OWNER,
    }
)


def permissions_for(role: Role) -> frozenset[Permission]:
    """Return the permission set granted to ``role``."""
    return ROLE_PERMISSIONS[role]


def has_permission(role: Role, permission: Permission) -> bool:
    """True when ``role`` grants ``permission``."""
    return permission in ROLE_PERMISSIONS[role]
