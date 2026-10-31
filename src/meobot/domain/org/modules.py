"""The modules whose permissions departments (and personal grants) hold, in one table.

The modules declare their permission vocabulary here - the order flow, the
PR module, the resources module, the points reports and the report assistant
on Telegram - and every layer of authorization reads this table alone:

* the **department** layer: ``department_permissions`` (module x permission x
  position -> scope), edited on "Phòng ban";
* the **personal** layer: ``user_permissions`` (one person, one permission,
  one scope), edited on the person's "Quyền" page by whoever may;
* the **system** layer: OWNER and ADMIN hold everything, everywhere.

Every module also takes the common permission
:attr:`CommonPermission.MANAGE_PERMISSIONS` - *cấp quyền cho người khác* -
with scope ``OWN`` = the members of the departments one heads, ``ALL`` =
everybody. It is what lets a Trưởng phòng PR shape their own team's rights
without being an administrator.

The PR module's three review gates (``GRANT_BACKED``) are not department
cells: they are held per person, over a content-type and channel scope, in
``pr_user_capabilities`` - the one place a permission carries a filter.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from meobot.domain.assistant.reports import (
    ASSISTANT_OWNER_ONLY,
    ASSISTANT_PERMISSION_LABELS,
    MODULE_ASSISTANT,
    AssistantPermission,
    assistant_allowed_scopes,
)
from meobot.domain.identity.models import Role
from meobot.domain.orders.permissions import (
    OWN_MEANING,
    PERMISSION_LABELS,
    AdsPermission,
    allowed_scopes,
)
from meobot.domain.org.models import MODULE_ORDER, MODULE_PR, DepartmentFunction, Position
from meobot.domain.performance.models import (
    MODULE_PERFORMANCE,
    PERFORMANCE_OWN_MEANING,
    PERFORMANCE_OWNER_ONLY,
    PERFORMANCE_PERMISSION_LABELS,
    PERFORMANCE_SCOPE_LABELS,
    PerformancePermission,
    default_performance_grants,
    performance_allowed_scopes,
)
from meobot.domain.pr.membership import CAPABILITY_INFO, role_capabilities
from meobot.domain.pr.policy import GRANT_BACKED, PrCapability
from meobot.domain.resources.models import (
    MODULE_RESOURCES,
    RESOURCE_OWN_MEANING,
    RESOURCE_PERMISSION_LABELS,
    RESOURCE_SCOPE_LABELS,
    ResourcePermission,
    default_resource_grants,
    resource_allowed_scopes,
)


class CommonPermission(StrEnum):
    """Permissions every module has, on top of its own."""

    #: Grant and revoke this module's permissions for other people: ``OWN``
    #: = members of the departments one heads, ``ALL`` = anybody but
    #: OWNER / ADMIN. Never grantable through the personal layer itself.
    MANAGE_PERMISSIONS = "MANAGE_PERMISSIONS"


COMMON_LABELS: Mapping[CommonPermission, str] = MappingProxyType(
    {CommonPermission.MANAGE_PERMISSIONS: "Cấp / thu hồi quyền cho người khác"}
)
COMMON_OWN_MEANING: Mapping[CommonPermission, str] = MappingProxyType(
    {CommonPermission.MANAGE_PERMISSIONS: "Nhân viên của phòng mình làm trưởng phòng"}
)

SCOPES_ALL_ONLY = ("NONE", "ALL")
SCOPES_OWN_ALL = ("NONE", "OWN", "ALL")

#: The PR module's grant-backed review gates: personal, scoped, never a cell.
PR_PERSONAL_ONLY: frozenset[str] = frozenset(item.value for item in GRANT_BACKED)


@dataclass(frozen=True, slots=True)
class ModuleSpec:
    module: str
    label: str
    permissions: type[StrEnum]
    labels: Mapping[Any, str]
    own_meaning: Mapping[Any, str]
    scopes: Callable[[Any], Sequence[Any]]
    #: Permissions a department cell may not hold (held per person, scoped).
    personal_only: frozenset[str] = frozenset()
    #: What a department starts with in this module, by function and position.
    defaults: Callable[[DepartmentFunction], dict[Position, dict[str, str]]] | None = None
    #: A permission's own words for its scopes, where the generic ones mislead.
    scope_labels: Mapping[Any, Mapping[str, str]] = field(default_factory=dict)
    #: Permissions the OWNER alone holds by role (not an ADMIN) and grants.
    owner_only: frozenset[str] = frozenset()
    #: False: granted per person only - no department cell, not on "Phòng ban".
    department_cells: bool = True

    def permission(self, key: str) -> str:
        """The permission named ``key`` (the module's own, or a common one);
        ``ValueError`` when the module has none."""
        if key in {item.value for item in CommonPermission}:
            return key
        return self.permissions(key).value

    def allowed(self, permission: str) -> set[str]:
        if permission in {item.value for item in CommonPermission}:
            return set(SCOPES_OWN_ALL)
        return {
            str(getattr(scope, "value", scope))
            for scope in self.scopes(self.permissions(permission))
        }

    def label_of(self, permission: str) -> str:
        if permission in {item.value for item in CommonPermission}:
            return COMMON_LABELS[CommonPermission(permission)]
        return self.labels[self.permissions(permission)]

    def own_meaning_of(self, permission: str) -> str | None:
        if permission in {item.value for item in CommonPermission}:
            return COMMON_OWN_MEANING.get(CommonPermission(permission))
        return self.own_meaning.get(self.permissions(permission))

    def scope_labels_of(self, permission: str) -> dict[str, str] | None:
        if permission in {item.value for item in CommonPermission}:
            return None
        labels = self.scope_labels.get(self.permissions(permission))
        return dict(labels) if labels else None

    def permission_keys(self) -> list[str]:
        """Every permission of the module, the common ones last."""
        return [item.value for item in self.permissions] + [item.value for item in CommonPermission]

    def department_keys(self) -> list[str]:
        """The permissions a department cell may hold (none in a per-person module)."""
        if not self.department_cells:
            return []
        return [key for key in self.permission_keys() if key not in self.personal_only]

    def default_grants(self, function: DepartmentFunction) -> dict[Position, dict[str, str]]:
        return self.defaults(function) if self.defaults is not None else {}


def _pr_scopes(_permission: Any) -> tuple[str, ...]:
    return SCOPES_ALL_ONLY


def default_pr_grants(function: DepartmentFunction) -> dict[Position, dict[str, str]]:
    """What a PR department grants by position: a head what a Trưởng nhóm
    (``TEAM_LEAD``) held through the role matrix before 0066, a member what an
    ``EMPLOYEE`` held - plus, for the head, the right to shape the team's
    permissions. Ban Giám đốc sits in PR as members did. Other departments
    grant nothing in PR."""
    member = {capability.value: "ALL" for capability in role_capabilities(Role.EMPLOYEE)}
    if function is DepartmentFunction.PR:
        head = {capability.value: "ALL" for capability in role_capabilities(Role.TEAM_LEAD)}
        # 0067: the head keeps the roster - sees it and adds people to PR.
        head[PrCapability.PR_MEMBERS_VIEW.value] = "ALL"
        head[PrCapability.PR_MEMBERS_MANAGE.value] = "ALL"
        head[CommonPermission.MANAGE_PERMISSIONS.value] = "OWN"
        return {Position.HEAD: head, Position.MEMBER: member}
    if function is DepartmentFunction.BOARD:
        return {Position.HEAD: dict(member), Position.MEMBER: dict(member)}
    return {}


MODULES: Mapping[str, ModuleSpec] = MappingProxyType(
    {
        MODULE_ORDER: ModuleSpec(
            module=MODULE_ORDER,
            label="Luồng order",
            permissions=AdsPermission,
            labels=PERMISSION_LABELS,
            own_meaning=OWN_MEANING,
            scopes=allowed_scopes,
        ),
        MODULE_PR: ModuleSpec(
            module=MODULE_PR,
            label="Nội dung PR",
            permissions=PrCapability,
            labels=MappingProxyType(
                {capability: label for capability, (_domain, label) in CAPABILITY_INFO.items()}
            ),
            own_meaning=MappingProxyType({}),
            scopes=_pr_scopes,
            personal_only=PR_PERSONAL_ONLY,
            defaults=default_pr_grants,
        ),
        MODULE_RESOURCES: ModuleSpec(
            module=MODULE_RESOURCES,
            label="Quản lý tài nguyên",
            permissions=ResourcePermission,
            labels=RESOURCE_PERMISSION_LABELS,
            own_meaning=RESOURCE_OWN_MEANING,
            scopes=resource_allowed_scopes,
            scope_labels=RESOURCE_SCOPE_LABELS,
            defaults=default_resource_grants,
        ),
        MODULE_PERFORMANCE: ModuleSpec(
            module=MODULE_PERFORMANCE,
            label="Điểm hiệu suất",
            permissions=PerformancePermission,
            labels=PERFORMANCE_PERMISSION_LABELS,
            own_meaning=PERFORMANCE_OWN_MEANING,
            scopes=performance_allowed_scopes,
            scope_labels=PERFORMANCE_SCOPE_LABELS,
            defaults=default_performance_grants,
            owner_only=PERFORMANCE_OWNER_ONLY,
        ),
        MODULE_ASSISTANT: ModuleSpec(
            module=MODULE_ASSISTANT,
            label="Trợ lý AI",
            permissions=AssistantPermission,
            labels=ASSISTANT_PERMISSION_LABELS,
            own_meaning=MappingProxyType({}),
            scopes=assistant_allowed_scopes,
            owner_only=ASSISTANT_OWNER_ONLY,
            department_cells=False,
        ),
    }
)


def module_spec(module: str) -> ModuleSpec:
    """The module named ``module``; ``KeyError`` for an unknown one."""
    return MODULES[module]


__all__ = [
    "COMMON_LABELS",
    "MODULES",
    "PR_PERSONAL_ONLY",
    "SCOPES_ALL_ONLY",
    "SCOPES_OWN_ALL",
    "CommonPermission",
    "ModuleSpec",
    "default_pr_grants",
    "module_spec",
]
