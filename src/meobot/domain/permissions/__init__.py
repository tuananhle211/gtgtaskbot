"""Permission vocabulary and the role -> permission matrix."""

from meobot.domain.permissions.matrix import (
    ROLE_PERMISSIONS,
    Permission,
    has_permission,
    permissions_for,
)

__all__ = ["ROLE_PERMISSIONS", "Permission", "has_permission", "permissions_for"]
