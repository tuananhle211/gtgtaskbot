"""Membership vocabulary for the web admin: what a base role grants, in words.

Phase 1 of *Thành viên & Phân quyền*. This module adds **no** rule: the
capability a role holds is :data:`~meobot.domain.pr.policy.CAPABILITY_PERMISSIONS`
composed with :data:`~meobot.domain.permissions.matrix.ROLE_PERMISSIONS`, and
that is what :func:`role_capabilities` reads. What it adds is the vocabulary a
screen needs to *show* the matrix - a Vietnamese label and a domain for each
capability - so the roles tab is a rendering of the model and never a second
copy of it.

Three concepts, kept apart because the product rule keeps them apart:

* **membership** - whether the person is in the system at all. A ``users`` row
  with :attr:`~meobot.domain.access.models.UserStatus.ACTIVE`;
* **base role** - one enum value on that row, which decides the default
  capabilities through the permission matrix;
* **scoped approval grant** - one of three review capabilities, held over a
  content-type and channel scope, additive and never a role.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType

from meobot.domain.identity.models import Role
from meobot.domain.permissions.matrix import Permission, has_permission
from meobot.domain.pr.policy import PrCapability, baseline_permission, requires_grant


class PrCapabilityDomain(StrEnum):
    """Where on the screen a capability is filed. Presentation, not policy."""

    CONTENT = "CONTENT"
    REVIEW = "REVIEW"
    PRODUCTION = "PRODUCTION"
    PUBLICATION = "PUBLICATION"
    TASKS = "TASKS"
    CHANNELS = "CHANNELS"
    WORK = "WORK"
    KPI = "KPI"


CAPABILITY_DOMAIN_LABELS: Mapping[PrCapabilityDomain, str] = MappingProxyType(
    {
        PrCapabilityDomain.CONTENT: "Nội dung",
        PrCapabilityDomain.REVIEW: "Duyệt nội dung",
        PrCapabilityDomain.PRODUCTION: "Sản xuất",
        PrCapabilityDomain.PUBLICATION: "Xuất bản",
        PrCapabilityDomain.TASKS: "Task",
        PrCapabilityDomain.CHANNELS: "Kênh",
        PrCapabilityDomain.WORK: "Công việc",
        PrCapabilityDomain.KPI: "KPI & hiệu suất",
    }
)

#: Every PR capability, its domain and its label. A capability missing here
#: is a failing test, not a blank row.
CAPABILITY_INFO: Mapping[PrCapability, tuple[PrCapabilityDomain, str]] = MappingProxyType(
    {
        PrCapability.PR_CONTENT_CREATE: (PrCapabilityDomain.CONTENT, "Tạo nội dung"),
        PrCapability.PR_CONTENT_EDIT: (PrCapabilityDomain.CONTENT, "Sửa nội dung"),
        PrCapability.PR_CONTENT_TRANSITION: (
            PrCapabilityDomain.CONTENT,
            "Chuyển bước nội dung (kể cả lưu trữ)",
        ),
        PrCapability.PR_CONTENT_CANCEL: (PrCapabilityDomain.CONTENT, "Hủy nội dung"),
        PrCapability.PR_CONTENT_DELETE: (PrCapabilityDomain.CONTENT, "Xóa nội dung chưa đăng"),
        PrCapability.PR_TEAM_LEAD_REVIEW: (PrCapabilityDomain.REVIEW, "Duyệt Trưởng nhóm"),
        PrCapability.PR_HEAD_REVIEW: (PrCapabilityDomain.REVIEW, "Duyệt Trưởng phòng"),
        PrCapability.PR_INTERNAL_REVIEW: (PrCapabilityDomain.REVIEW, "Duyệt nội bộ"),
        PrCapability.PR_PRODUCTION_ASSIGN: (PrCapabilityDomain.PRODUCTION, "Phân công sản xuất"),
        PrCapability.PR_PRODUCTION_EXECUTE: (PrCapabilityDomain.PRODUCTION, "Làm sản xuất"),
        PrCapability.PR_PUBLICATION_CREATE: (
            PrCapabilityDomain.PUBLICATION,
            "Ghi nhận bài đăng (nội dung mình xem được)",
        ),
        PrCapability.PR_PUBLICATION_REGISTER: (
            PrCapabilityDomain.PUBLICATION,
            "Ghi nhận / sửa bài đăng của người khác",
        ),
        PrCapability.PR_TASK_MANAGE: (PrCapabilityDomain.TASKS, "Quản lý task"),
        PrCapability.PR_CHANNEL_MANAGE: (PrCapabilityDomain.CHANNELS, "Quản lý kênh"),
        PrCapability.PR_WORK_EXECUTE: (PrCapabilityDomain.WORK, "Nhận và báo cáo công việc"),
        PrCapability.PR_WORK_MANAGE: (PrCapabilityDomain.WORK, "Giao và chấp nhận công việc"),
        PrCapability.PR_WORK_VALIDATE: (PrCapabilityDomain.WORK, "Xác nhận công việc hoàn thành"),
        PrCapability.PR_WORK_VIEW_ALL: (PrCapabilityDomain.WORK, "Xem công việc của cả phòng"),
        PrCapability.PR_WORK_CONFIGURE: (
            PrCapabilityDomain.KPI,
            "Cấu hình loại việc, KPI và định mức",
        ),
        PrCapability.PR_PERFORMANCE_REVIEW: (PrCapabilityDomain.KPI, "Đánh giá hiệu suất tháng"),
    }
)


def capability_label(capability: PrCapability) -> str:
    return CAPABILITY_INFO[capability][1]


def capability_domain(capability: PrCapability) -> PrCapabilityDomain:
    return CAPABILITY_INFO[capability][0]


def role_capabilities(role: Role) -> frozenset[PrCapability]:
    """The capabilities a base role holds **through the permission matrix**.

    Read off the same two tables authorization reads - the baseline permission
    per capability and the permission matrix per role - and only those. The
    three grant-backed review capabilities are never here: a role gives no
    approval right, whatever its rank, which is the whole reason grants exist.
    """
    return frozenset(
        capability
        for capability in PrCapability
        if not requires_grant(capability) and has_permission(role, baseline_permission(capability))
    )


#: The base roles a web administrator may set. ``OWNER`` is configuration -
#: ``MEOBOT_OWNER_TELEGRAM_ID`` - and is neither offered nor accepted.
ASSIGNABLE_ROLES: tuple[Role, ...] = (Role.EMPLOYEE, Role.TEAM_LEAD, Role.ADMIN)

#: The one permission every admin surface of this module reads by. ``user.read``
#: is what already means "may see who works here", and it reaches exactly the
#: roles that manage people.
MEMBER_READ_PERMISSION = Permission.USER_READ


__all__: list[str] = [
    "ASSIGNABLE_ROLES",
    "CAPABILITY_DOMAIN_LABELS",
    "CAPABILITY_INFO",
    "MEMBER_READ_PERMISSION",
    "PrCapabilityDomain",
    "capability_domain",
    "capability_label",
    "role_capabilities",
]
