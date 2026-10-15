"""The modules whose permissions departments grant, in one table.

Each module declares its permission enum, the words for each permission, what
``OWN`` means where it is allowed, and which scopes each permission takes.
The departments screen draws one grant table per module; the department
service validates a cell against this table alone.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from meobot.domain.orders.permissions import (
    OWN_MEANING,
    PERMISSION_LABELS,
    AdsPermission,
    allowed_scopes,
)
from meobot.domain.org.models import MODULE_ORDER
from meobot.domain.resources.models import (
    MODULE_RESOURCES,
    RESOURCE_OWN_MEANING,
    RESOURCE_PERMISSION_LABELS,
    ResourcePermission,
    resource_allowed_scopes,
)


@dataclass(frozen=True, slots=True)
class ModuleSpec:
    module: str
    label: str
    permissions: type[StrEnum]
    labels: Mapping[Any, str]
    own_meaning: Mapping[Any, str]
    scopes: Callable[[Any], Sequence[Any]]

    def permission(self, key: str) -> StrEnum:
        """The permission named ``key``; ``ValueError`` when the module has none."""
        return self.permissions(key)

    def allowed(self, permission: StrEnum) -> set[str]:
        return {scope.value for scope in self.scopes(permission)}


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
        MODULE_RESOURCES: ModuleSpec(
            module=MODULE_RESOURCES,
            label="Quản lý tài nguyên",
            permissions=ResourcePermission,
            labels=RESOURCE_PERMISSION_LABELS,
            own_meaning=RESOURCE_OWN_MEANING,
            scopes=resource_allowed_scopes,
        ),
    }
)


def module_spec(module: str) -> ModuleSpec:
    """The module named ``module``; ``KeyError`` for an unknown one."""
    return MODULES[module]


__all__ = ["MODULES", "ModuleSpec", "module_spec"]
