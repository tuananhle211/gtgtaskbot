"""Errors of the points reports. Core subclasses: a refused report is a 403."""

from __future__ import annotations

from meobot.core.errors import AuthorizationError, NotFoundError, ValidationError


class PerformanceForbiddenError(AuthorizationError):
    """The caller may not see these points (``details.reason`` too)."""

    code = "performance_forbidden"

    def __init__(self, message: str = "Bạn không xem được điểm này.") -> None:
        super().__init__(message, details={"reason": "performance_forbidden"})


class PerformancePersonNotFoundError(NotFoundError):
    code = "performance_person_not_found"

    def __init__(self) -> None:
        super().__init__(
            "Không tìm thấy nhân viên.", details={"reason": "performance_person_not_found"}
        )


class PerformanceCoefficientForbiddenError(AuthorizationError):
    """Only the OWNER (or a ``PERFORMANCE COEFFICIENT`` holder) sets coefficients."""

    code = "performance_coefficient_forbidden"

    def __init__(self) -> None:
        super().__init__(
            "Chỉ chủ sở hữu (OWNER) cấu hình được hệ số điểm.",
            details={"reason": "performance_coefficient_forbidden"},
        )


class PerformanceCoefficientInvalidError(ValidationError):
    code = "coefficient_invalid"

    def __init__(self, message: str, *, field: str = "coefficient") -> None:
        super().__init__(message, details={"reason": "coefficient_invalid", "field": field})


__all__ = [
    "PerformanceCoefficientForbiddenError",
    "PerformanceCoefficientInvalidError",
    "PerformanceForbiddenError",
    "PerformancePersonNotFoundError",
]
