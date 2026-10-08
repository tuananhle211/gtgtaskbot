"""The Ads unit's permissions: a role-based matrix, the counterpart of PR's.

PR decides with named capabilities (``PR_TEAM_LEAD_REVIEW``,
``PR_PRODUCTION_ASSIGN``...) checked against a role baseline. Ads now does the
same, without PR's per-person grants: every management action is an
:class:`AdsPermission`, and what each Ads role holds is one cell of a matrix -

* ``NONE``  - not at all;
* ``OWN``   - inside their own function (a Leader's or staff member's nodes),
  or, for the orderer, on their own orders;
* ``ALL``   - on every order of the unit.

The matrix is the unit's (``org_units.settings["permissions"]``), edited on
the admin page; what is not stored falls back to :data:`DEFAULT_MATRIX`. The
OWNER is not a row: they hold everything, everywhere.

Doing the work itself - accepting, handing in, attaching the link,
resubmitting one's own returned order, deciding the final review of one's own
order - is not a permission. It follows from being the assignee or the
orderer, exactly as PR's producer rule does. ``FINAL_REVIEW`` is the
stand-in: who holds it may decide the final review in the orderer's place.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from meobot.domain.orders.models import OrderNodeType


class AdsPermission(StrEnum):
    VIEW = "VIEW"
    ORDER_CREATE = "ORDER_CREATE"
    ORDER_APPROVE = "ORDER_APPROVE"
    NODE_ASSIGN = "NODE_ASSIGN"
    NODE_REVIEW = "NODE_REVIEW"
    VIDEO_REVIEW = "VIDEO_REVIEW"
    FINAL_REVIEW = "FINAL_REVIEW"
    PRIORITY = "PRIORITY"
    CANCEL = "CANCEL"


class AdsScope(StrEnum):
    NONE = "NONE"
    OWN = "OWN"
    ALL = "ALL"


class AdsRoleKey(StrEnum):
    """The matrix's columns. One person may hold several (an ADMIN tagged as
    a Leader holds ADMIN and LEAD); their permissions are the union."""

    HEAD = "HEAD"
    ADMIN = "ADMIN"
    LEAD = "LEAD"
    STAFF = "STAFF"
    ORDERER = "ORDERER"


PERMISSION_LABELS: Mapping[AdsPermission, str] = MappingProxyType(
    {
        AdsPermission.VIEW: "Xem order",
        AdsPermission.ORDER_CREATE: "Tạo order",
        AdsPermission.ORDER_APPROVE: "Duyệt / trả order",
        AdsPermission.NODE_ASSIGN: "Giao việc",
        AdsPermission.NODE_REVIEW: "Duyệt / trả bài nộp",
        AdsPermission.VIDEO_REVIEW: "Duyệt video (quy trình có Biên kịch)",
        AdsPermission.FINAL_REVIEW: "Duyệt final thay người order",
        AdsPermission.PRIORITY: "Đặt ưu tiên",
        AdsPermission.CANCEL: "Huỷ order",
    }
)

#: What ``OWN`` means for each permission; absent = the cell is yes/no only.
OWN_MEANING: Mapping[AdsPermission, str] = MappingProxyType(
    {
        AdsPermission.VIEW: "Order của mình, của ban mình hoặc được giao",
        AdsPermission.NODE_ASSIGN: "Công đoạn của ban mình",
        AdsPermission.NODE_REVIEW: "Công đoạn của ban mình",
        AdsPermission.VIDEO_REVIEW: "Khi ban mình là Biên tập",
        AdsPermission.CANCEL: "Order của mình, trước khi được duyệt",
    }
)

ROLE_LABELS: Mapping[AdsRoleKey, str] = MappingProxyType(
    {
        AdsRoleKey.HEAD: "Trưởng phòng ORD",
        AdsRoleKey.ADMIN: "Admin",
        AdsRoleKey.LEAD: "Trưởng phòng ban (Biên kịch / Design / Dựng)",
        AdsRoleKey.STAFF: "Nhân viên ban",
        AdsRoleKey.ORDERER: "Marketing",
    }
)

SCOPE_LABELS: Mapping[AdsScope, str] = MappingProxyType(
    {AdsScope.NONE: "Không", AdsScope.OWN: "Trong ban mình", AdsScope.ALL: "Tất cả"}
)

_ALL = AdsScope.ALL
_OWN = AdsScope.OWN

#: Managers run every node of every order; a Leader runs their function;
#: staff do the work they are given; Marketing orders.
DEFAULT_MATRIX: Mapping[AdsRoleKey, Mapping[AdsPermission, AdsScope]] = MappingProxyType(
    {
        AdsRoleKey.HEAD: MappingProxyType(dict.fromkeys(AdsPermission, _ALL)),
        AdsRoleKey.ADMIN: MappingProxyType(dict.fromkeys(AdsPermission, _ALL)),
        AdsRoleKey.LEAD: MappingProxyType(
            {
                AdsPermission.VIEW: _OWN,
                AdsPermission.NODE_ASSIGN: _OWN,
                AdsPermission.NODE_REVIEW: _OWN,
                AdsPermission.VIDEO_REVIEW: _OWN,
            }
        ),
        AdsRoleKey.STAFF: MappingProxyType({AdsPermission.VIEW: _OWN}),
        AdsRoleKey.ORDERER: MappingProxyType(
            {
                AdsPermission.VIEW: _OWN,
                AdsPermission.ORDER_CREATE: _ALL,
                AdsPermission.CANCEL: _OWN,
            }
        ),
    }
)


class AdsPermissionMatrixError(ValueError):
    """A stored or submitted matrix names an unknown role, permission or scope."""


def allowed_scopes(permission: AdsPermission) -> tuple[AdsScope, ...]:
    if permission in OWN_MEANING:
        return (AdsScope.NONE, AdsScope.OWN, AdsScope.ALL)
    return (AdsScope.NONE, AdsScope.ALL)


def resolve_matrix(
    stored: Mapping[str, Mapping[str, str]] | None,
) -> dict[AdsRoleKey, dict[AdsPermission, AdsScope]]:
    """Defaults overlaid with what the unit stored. Raises on anything unknown."""
    matrix = {
        role: {
            permission: DEFAULT_MATRIX[role].get(permission, AdsScope.NONE)
            for permission in AdsPermission
        }
        for role in AdsRoleKey
    }
    for role_key, cells in (stored or {}).items():
        try:
            role = AdsRoleKey(role_key)
        except ValueError as error:
            raise AdsPermissionMatrixError(f"unknown role {role_key!r}") from error
        for permission_key, scope_key in cells.items():
            try:
                permission = AdsPermission(permission_key)
                scope = AdsScope(scope_key)
            except ValueError as error:
                raise AdsPermissionMatrixError(
                    f"unknown cell {permission_key!r}={scope_key!r}"
                ) from error
            if scope not in allowed_scopes(permission):
                raise AdsPermissionMatrixError(f"{permission.value} cannot be {scope.value}")
            matrix[role][permission] = scope
    return matrix


def normalise_matrix(stored: Mapping[str, Mapping[str, str]] | None) -> dict[str, dict[str, str]]:
    """The full matrix as plain strings, for storage and the API."""
    return {
        role.value: {permission.value: scope.value for permission, scope in cells.items()}
        for role, cells in resolve_matrix(stored).items()
    }


_ALL_NODES = frozenset(OrderNodeType)


@dataclass(frozen=True, slots=True)
class AdsPermissions:
    """One person's effective permissions inside Ads.

    ``everywhere`` holds the permissions granted ``ALL``; ``own_nodes`` the
    function nodes each ``OWN`` permission reaches; ``own_orders`` the
    permissions that reach the person's own orders (Marketing's ``OWN``).
    """

    everywhere: frozenset[AdsPermission] = frozenset()
    own_nodes: Mapping[AdsPermission, frozenset[OrderNodeType]] = field(default_factory=dict)
    own_orders: frozenset[AdsPermission] = frozenset()

    def allows(self, permission: AdsPermission) -> bool:
        """Granted on every order."""
        return permission in self.everywhere

    def nodes(self, permission: AdsPermission) -> frozenset[OrderNodeType]:
        """The node types this permission reaches."""
        if permission in self.everywhere:
            return _ALL_NODES
        return self.own_nodes.get(permission, frozenset())

    def on_own_order(self, permission: AdsPermission) -> bool:
        return permission in self.everywhere or permission in self.own_orders

    @classmethod
    def full(cls) -> AdsPermissions:
        return cls(everywhere=frozenset(AdsPermission))

    @classmethod
    def compute(
        cls,
        matrix: Mapping[AdsRoleKey, Mapping[AdsPermission, AdsScope]],
        *,
        roles: Iterable[AdsRoleKey],
        function_nodes: frozenset[OrderNodeType],
        lead_nodes: frozenset[OrderNodeType],
    ) -> AdsPermissions:
        everywhere: set[AdsPermission] = set()
        own_nodes: dict[AdsPermission, set[OrderNodeType]] = {}
        own_orders: set[AdsPermission] = set()
        for role in roles:
            nodes = lead_nodes if role is AdsRoleKey.LEAD else function_nodes
            for permission, scope in matrix[role].items():
                if scope is AdsScope.ALL:
                    everywhere.add(permission)
                elif scope is AdsScope.OWN:
                    if role is AdsRoleKey.ORDERER or permission is AdsPermission.CANCEL:
                        own_orders.add(permission)
                    if role is not AdsRoleKey.ORDERER:
                        own_nodes.setdefault(permission, set()).update(nodes)
        return cls(
            everywhere=frozenset(everywhere),
            own_nodes={key: frozenset(value) for key, value in own_nodes.items()},
            own_orders=frozenset(own_orders),
        )


def catalog() -> list[dict[str, Any]]:
    """The permissions as the admin page lists them."""
    return [
        {
            "key": permission.value,
            "label": PERMISSION_LABELS[permission],
            "own_meaning": OWN_MEANING.get(permission),
            "scopes": [scope.value for scope in allowed_scopes(permission)],
        }
        for permission in AdsPermission
    ]


__all__ = [
    "DEFAULT_MATRIX",
    "OWN_MEANING",
    "PERMISSION_LABELS",
    "ROLE_LABELS",
    "SCOPE_LABELS",
    "AdsPermission",
    "AdsPermissionMatrixError",
    "AdsPermissions",
    "AdsRoleKey",
    "AdsScope",
    "allowed_scopes",
    "catalog",
    "normalise_matrix",
    "resolve_matrix",
]
