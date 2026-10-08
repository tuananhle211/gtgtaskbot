"""Errors of the order engine. Subclasses of the core errors, so the API's
status map needs no new entry: not found → 404, a stale button → 409, a move
the state does not allow → 409, a person who may not → 403, bad input → 422."""

from __future__ import annotations

from meobot.core.errors import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ValidationError,
    WorkflowStateError,
)


class OrderNotFoundError(NotFoundError):
    """No such order, or one outside this person's scope - the same sentence."""

    code = "order_not_found"


class OrderStaleVersionError(ConflictError):
    """The button was pressed on an order somebody else already moved."""

    code = "order_stale_version"


class OrderStateError(WorkflowStateError):
    """The action exists, but not from where the order is."""

    code = "order_invalid_state"


class OrderActionNotAllowedError(AuthorizationError):
    """The action exists here, but not for this person."""

    code = "order_forbidden"


class OrderValidationError(ValidationError):
    code = "order_validation_error"


__all__ = [
    "OrderActionNotAllowedError",
    "OrderNotFoundError",
    "OrderStaleVersionError",
    "OrderStateError",
    "OrderValidationError",
]
