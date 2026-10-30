"""Errors of the points reports. Core subclasses: a refused report is a 403."""

from __future__ import annotations

from meobot.core.errors import AuthorizationError, ConflictError, NotFoundError, ValidationError


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


class PerformanceQuotaForbiddenError(AuthorizationError):
    """Only the OWNER (or a ``PERFORMANCE COEFFICIENT`` holder) sets the quota."""

    code = "performance_quota_forbidden"

    def __init__(self) -> None:
        super().__init__(
            "Chỉ chủ sở hữu (OWNER) cấu hình được định mức.",
            details={"reason": "performance_quota_forbidden"},
        )


class PerformanceQuotaInvalidError(ValidationError):
    code = "quota_invalid"

    def __init__(self, message: str, *, field: str) -> None:
        super().__init__(message, details={"reason": "quota_invalid", "field": field})


class PrPointsForbiddenError(AuthorizationError):
    """Not in PR, not the person the stage is theirs to claim, or not a
    Trưởng phòng PR deciding."""

    code = "pr_points_forbidden"

    def __init__(self, message: str = "Bạn không làm được việc này với điểm PR.") -> None:
        super().__init__(message, details={"reason": "pr_points_forbidden"})


class PrPointsInvalidError(ValidationError):
    code = "pr_points_invalid"

    def __init__(self, message: str, *, reason: str = "pr_points_invalid") -> None:
        super().__init__(message, details={"reason": reason})


class PrPointsNotFoundError(NotFoundError):
    code = "pr_points_not_found"

    def __init__(self) -> None:
        super().__init__("Không tìm thấy.", details={"reason": "pr_points_not_found"})


class PrClaimDecidedError(ConflictError):
    code = "pr_claim_decided"

    def __init__(self) -> None:
        super().__init__(
            "Phiếu điểm này đã được duyệt, không sửa được nữa.",
            details={"reason": "pr_claim_decided"},
        )


__all__ = [
    "PerformanceCoefficientForbiddenError",
    "PerformanceCoefficientInvalidError",
    "PerformanceForbiddenError",
    "PerformancePersonNotFoundError",
    "PerformanceQuotaForbiddenError",
    "PerformanceQuotaInvalidError",
    "PrClaimDecidedError",
    "PrPointsForbiddenError",
    "PrPointsInvalidError",
    "PrPointsNotFoundError",
]
