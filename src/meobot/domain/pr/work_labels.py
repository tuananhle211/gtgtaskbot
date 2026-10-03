"""The Work Ledger in Vietnamese. One wording per value, in one place.

M1. Separate from :mod:`meobot.domain.pr.labels`, which is already the content
module's table and has no reader in common with this one.

Every label here is sent to the client on a ``*_label`` field, so the browser
holds no second copy and a Telegram tool, a web panel and a CSV export all say
the same word for the same state. The rule the whole panel follows: **the server
owns the vocabulary, the client owns the layout.**

Two wordings are load-bearing
------------------------------

``COMPLETED`` is *"Chờ xác nhận"*, not *"Đã xong"*. The status means a
contributor said the work is finished and nobody has confirmed it, and calling
that "done" on a screen would undo in one word the boundary the whole milestone
is built around.

``COUNTED`` is *"Đã ghi nhận"* - recorded - and deliberately not *"Đã tính
điểm"*. Nothing has been scored: M1 records that work is valid completed work
and stops, and a label promising points would be describing a feature that does
not exist.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from meobot.domain.pr.recurring import (
    PrRecurringFrequency,
    PrRecurringOccurrenceState,
    PrRecurringTemplateStatus,
)
from meobot.domain.pr.work import (
    PrWorkCategory,
    PrWorkContributionRole,
    PrWorkCountStatus,
    PrWorkEventType,
    PrWorkSourceType,
    PrWorkStatus,
    PrWorkUnit,
)
from meobot.domain.pr.work_results import PrWorkExclusionKind, PrWorkResultSource

#: The seven statuses of a job.
WORK_STATUS_LABELS: Mapping[PrWorkStatus, str] = MappingProxyType(
    {
        PrWorkStatus.PROPOSED: "Chờ duyệt đề xuất",
        PrWorkStatus.ACCEPTED: "Được giao",
        PrWorkStatus.IN_PROGRESS: "Đang làm",
        # Not "Đã xong". See the module docstring - this is the word that keeps
        # the anti-gaming boundary visible on a screen.
        PrWorkStatus.COMPLETED: "Chờ xác nhận",
        PrWorkStatus.APPROVED: "Đã xác nhận",
        PrWorkStatus.REJECTED: "Không được chấp nhận",
        PrWorkStatus.CANCELLED: "Đã hủy",
    }
)

#: The order a client offers statuses in. Presentation only, and carrying no
#: business meaning - the transition matrix decides what may follow what.
WORK_STATUS_ORDER: tuple[PrWorkStatus, ...] = (
    PrWorkStatus.PROPOSED,
    PrWorkStatus.ACCEPTED,
    PrWorkStatus.IN_PROGRESS,
    PrWorkStatus.COMPLETED,
    PrWorkStatus.APPROVED,
    PrWorkStatus.REJECTED,
    PrWorkStatus.CANCELLED,
)

WORK_CATEGORY_LABELS: Mapping[PrWorkCategory, str] = MappingProxyType(
    {
        PrWorkCategory.CONTENT: "Nội dung",
        PrWorkCategory.PRODUCTION: "Sản xuất",
        PrWorkCategory.DISTRIBUTION: "Phân phối",
        PrWorkCategory.COMMUNITY: "Cộng đồng",
        PrWorkCategory.PR_EVENT: "PR & sự kiện",
        PrWorkCategory.OPERATIONS: "Vận hành",
        PrWorkCategory.RESEARCH: "Nghiên cứu",
        # A fallback, and the label says so rather than pretending to be a
        # category: a report with a large "Khác" bar is telling somebody a work
        # type is missing.
        PrWorkCategory.OTHER: "Khác (chưa phân loại)",
    }
)

WORK_UNIT_LABELS: Mapping[PrWorkUnit, str] = MappingProxyType(
    {
        PrWorkUnit.ITEM: "sản phẩm",
        PrWorkUnit.VIDEO: "video",
        PrWorkUnit.POST: "bài đăng",
        PrWorkUnit.ARTICLE: "bài viết",
        PrWorkUnit.COMMENT: "bình luận",
        PrWorkUnit.MESSAGE: "tin nhắn",
        PrWorkUnit.ACCOUNT: "tài khoản",
        PrWorkUnit.SESSION: "buổi",
        PrWorkUnit.HOUR: "giờ",
        PrWorkUnit.DAY: "ngày công",
        PrWorkUnit.CUSTOMER: "khách hàng",
        PrWorkUnit.SCRIPT: "kịch bản",
        PrWorkUnit.ORDER: "đơn hàng",
    }
)

WORK_SOURCE_LABELS: Mapping[PrWorkSourceType, str] = MappingProxyType(
    {
        PrWorkSourceType.MANUAL: "Nhập thủ công",
        PrWorkSourceType.CONTENT: "Từ quy trình nội dung",
        PrWorkSourceType.TASK: "Từ task",
        PrWorkSourceType.RECURRING: "Việc lặp lại",
        PrWorkSourceType.SYSTEM: "Hệ thống tạo",
    }
)

#: Where a result came from. Period-container patch.
WORK_RESULT_SOURCE_LABELS: Mapping[PrWorkResultSource, str] = MappingProxyType(
    {
        PrWorkResultSource.MANUAL: "Tự báo cáo",
        PrWorkResultSource.CONTENT: "Từ quy trình nội dung",
        PrWorkResultSource.SEEDING: "Từ seeding",
        PrWorkResultSource.CRM: "Từ CRM",
        PrWorkResultSource.SYSTEM: "Hệ thống ghi nhận",
        PrWorkResultSource.OTHER: "Nguồn khác",
    }
)


def work_result_source_label(source: PrWorkResultSource) -> str:
    return WORK_RESULT_SOURCE_LABELS.get(source, source.value)


#: What a person's share is called.
WORK_CONTRIBUTION_ROLE_LABELS: Mapping[PrWorkContributionRole, str] = MappingProxyType(
    {
        PrWorkContributionRole.PRIMARY: "Phụ trách chính",
        PrWorkContributionRole.CONTRIBUTOR: "Tham gia",
        PrWorkContributionRole.SUPPORT: "Hỗ trợ",
    }
)

#: Whether one person's share is valid completed work.
COUNT_STATUS_LABELS: Mapping[PrWorkCountStatus, str] = MappingProxyType(
    {
        PrWorkCountStatus.PENDING: "Chưa ghi nhận",
        # "Ghi nhận", not "tính điểm". Nothing has been scored.
        PrWorkCountStatus.COUNTED: "Đã ghi nhận",
        PrWorkCountStatus.EXCLUDED: "Không tính",
    }
)

#: The user-facing timeline, phrased as things that happened to the work.
WORK_EVENT_LABELS: Mapping[PrWorkEventType, str] = MappingProxyType(
    {
        PrWorkEventType.CREATED: "Đã tạo",
        PrWorkEventType.PROPOSED: "Đã đề xuất",
        PrWorkEventType.ACCEPTED: "Đã chấp nhận đề xuất",
        PrWorkEventType.REJECTED: "Đã từ chối đề xuất",
        PrWorkEventType.ASSIGNED: "Đã giao việc",
        PrWorkEventType.CONTRIBUTOR_ADDED: "Thêm người thực hiện",
        PrWorkEventType.CONTRIBUTOR_REMOVED: "Gỡ người thực hiện",
        PrWorkEventType.STARTED: "Bắt đầu làm",
        PrWorkEventType.COMPLETED: "Báo hoàn thành",
        PrWorkEventType.REOPENED: "Trả lại để làm tiếp",
        PrWorkEventType.APPROVED: "Đã xác nhận hoàn thành",
        PrWorkEventType.COUNTED: "Ghi nhận vào công việc",
        PrWorkEventType.EXCLUDED: "Không tính vào công việc",
        PrWorkEventType.DEADLINE_CHANGED: "Đổi hạn",
        PrWorkEventType.PRIORITY_CHANGED: "Đổi mức ưu tiên",
        PrWorkEventType.EVIDENCE_ADDED: "Thêm minh chứng",
        PrWorkEventType.EVIDENCE_REMOVED: "Gỡ minh chứng",
        PrWorkEventType.CANCELLED: "Đã hủy",
        PrWorkEventType.RESULT_REPORTED: "Báo cáo kết quả",
        PrWorkEventType.RESULT_COUNTED: "Xác nhận kết quả",
        PrWorkEventType.RESULT_EXCLUDED: "Loại bỏ kết quả",
        PrWorkEventType.RESULT_WITHDRAWN: "Rút lại kết quả",
        PrWorkEventType.RESULT_REJECTED: "Từ chối kết quả",
        PrWorkEventType.RESULT_RECONSIDERED: "Mở lại để xem xét",
        PrWorkEventType.RESULT_ADMIN_REMOVED: "Xóa kết quả khỏi ghi nhận",
    }
)

#: A result's state as the row reads it. ``0041``: an excluded result is named
#: by **why** it is out, because "đã từ chối" and "đã xóa khỏi ghi nhận" call
#: for different next steps, and neither should read as the other.
WORK_RESULT_STATUS_LABELS: Mapping[PrWorkCountStatus, str] = MappingProxyType(
    {
        PrWorkCountStatus.PENDING: "Chờ xác nhận",
        PrWorkCountStatus.COUNTED: "Đã ghi nhận",
        # A legacy exclusion whose author the row does not record.
        PrWorkCountStatus.EXCLUDED: "Đã loại bỏ",
    }
)

WORK_EXCLUSION_KIND_LABELS: Mapping[PrWorkExclusionKind, str] = MappingProxyType(
    {
        PrWorkExclusionKind.ADMIN_REMOVED: "Đã xóa khỏi ghi nhận",
        PrWorkExclusionKind.VALIDATOR_REJECTED: "Đã từ chối",
        PrWorkExclusionKind.SOURCE_REVERSED: "Không còn đủ điều kiện",
    }
)


def work_result_status_label(
    status: PrWorkCountStatus, kind: PrWorkExclusionKind | None = None
) -> str:
    """What a result row says about itself. The kind names an exclusion."""
    if status is PrWorkCountStatus.EXCLUDED and kind is not None:
        return WORK_EXCLUSION_KIND_LABELS.get(kind, kind.value)
    return WORK_RESULT_STATUS_LABELS.get(status, status.value)


def work_exclusion_kind_label(kind: PrWorkExclusionKind) -> str:
    return WORK_EXCLUSION_KIND_LABELS.get(kind, kind.value)


def work_status_label(status: PrWorkStatus) -> str:
    """The Vietnamese name of a work status, or its code if somebody adds one."""
    return WORK_STATUS_LABELS.get(status, status.value)


def work_category_label(category: PrWorkCategory) -> str:
    return WORK_CATEGORY_LABELS.get(category, category.value)


def work_unit_label(unit: PrWorkUnit) -> str:
    return WORK_UNIT_LABELS.get(unit, unit.value)


def work_source_label(source: PrWorkSourceType) -> str:
    return WORK_SOURCE_LABELS.get(source, source.value)


def work_contribution_role_label(role: PrWorkContributionRole) -> str:
    return WORK_CONTRIBUTION_ROLE_LABELS.get(role, role.value)


def count_status_label(status: PrWorkCountStatus) -> str:
    return COUNT_STATUS_LABELS.get(status, status.value)


#: Why one row was kept out of a bulk validation. **M4A.**
#:
#: Here rather than in the frontend because the *reason* is a domain fact and
#: the sentence is its name - the same argument
#: :data:`WORK_STATUS_LABELS` makes. A client that met an unknown reason would
#: otherwise print a raw token like ``self_validation`` at somebody.
BULK_VALIDATION_REASONS: Mapping[str, str] = MappingProxyType(
    {
        "missing": "Công việc không còn tồn tại.",
        "self_validation": "Bạn có tham gia công việc này nên không thể tự xác nhận.",
        "not_completed": "Công việc chưa ở trạng thái chờ xác nhận.",
        "evidence_required": "Loại công việc này bắt buộc có minh chứng.",
    }
)


def bulk_validation_reason_label(reason: str) -> str:
    """Why a row cannot be validated, as a sentence. See ``BULK_VALIDATION_REASONS``."""
    return BULK_VALIDATION_REASONS.get(reason, reason)


#: The four states of a recurring template. **M4B.**
#:
#: Product words, not scheduler words: a manager reads "Đang chạy", never
#: ``ACTIVE``, and never a cursor, a cron expression or an occurrence state.
RECURRING_TEMPLATE_STATUS_LABELS: Mapping[PrRecurringTemplateStatus, str] = MappingProxyType(
    {
        PrRecurringTemplateStatus.DRAFT: "Nháp",
        PrRecurringTemplateStatus.ACTIVE: "Đang chạy",
        PrRecurringTemplateStatus.PAUSED: "Tạm dừng",
        PrRecurringTemplateStatus.ENDED: "Đã kết thúc",
    }
)

#: The order a client offers template states in. Presentation only.
RECURRING_TEMPLATE_STATUS_ORDER: tuple[PrRecurringTemplateStatus, ...] = (
    PrRecurringTemplateStatus.ACTIVE,
    PrRecurringTemplateStatus.PAUSED,
    PrRecurringTemplateStatus.DRAFT,
    PrRecurringTemplateStatus.ENDED,
)

#: How often a template fires. **M4B.**
RECURRING_FREQUENCY_LABELS: Mapping[PrRecurringFrequency, str] = MappingProxyType(
    {
        PrRecurringFrequency.DAILY: "Hằng ngày",
        PrRecurringFrequency.WEEKLY: "Hằng tuần",
        PrRecurringFrequency.MONTHLY: "Hằng tháng",
    }
)

#: What became of one scheduled occurrence. **M4B.**
#:
#: These are the only scheduler internals that reach a screen at all, and they
#: reach exactly one: the per-template history that answers *"why is there no
#: work for Tuesday"*. A manager configuring a routine never sees them, which is
#: why the create form has no field for any of them.
RECURRING_OCCURRENCE_STATE_LABELS: Mapping[PrRecurringOccurrenceState, str] = MappingProxyType(
    {
        PrRecurringOccurrenceState.PENDING: "Đang chờ tạo",
        PrRecurringOccurrenceState.GENERATED: "Đã tạo việc",
        # Names the *reason*, not the mechanism: "kỳ báo cáo đã chốt" is what a
        # manager can act on, while "cursor advanced past a locked period" is
        # not a sentence anybody outside this module should have to read.
        PrRecurringOccurrenceState.SKIPPED_CLOSED_PERIOD: "Bỏ qua - kỳ báo cáo đã chốt",
        PrRecurringOccurrenceState.FAILED_RETRYABLE: "Lỗi - sẽ thử lại",
    }
)


def recurring_template_status_label(status: PrRecurringTemplateStatus) -> str:
    """The Vietnamese name of a template state. See ``RECURRING_TEMPLATE_STATUS_LABELS``."""
    return RECURRING_TEMPLATE_STATUS_LABELS.get(status, status.value)


def recurring_frequency_label(frequency: PrRecurringFrequency) -> str:
    return RECURRING_FREQUENCY_LABELS.get(frequency, frequency.value)


def recurring_occurrence_state_label(state: PrRecurringOccurrenceState) -> str:
    return RECURRING_OCCURRENCE_STATE_LABELS.get(state, state.value)


def work_event_label(event: PrWorkEventType) -> str:
    return WORK_EVENT_LABELS.get(event, event.value)


__all__: list[str] = [
    "BULK_VALIDATION_REASONS",
    "COUNT_STATUS_LABELS",
    "RECURRING_FREQUENCY_LABELS",
    "RECURRING_OCCURRENCE_STATE_LABELS",
    "RECURRING_TEMPLATE_STATUS_LABELS",
    "RECURRING_TEMPLATE_STATUS_ORDER",
    "WORK_CATEGORY_LABELS",
    "WORK_CONTRIBUTION_ROLE_LABELS",
    "WORK_EVENT_LABELS",
    "WORK_SOURCE_LABELS",
    "WORK_STATUS_LABELS",
    "WORK_STATUS_ORDER",
    "WORK_UNIT_LABELS",
    "bulk_validation_reason_label",
    "count_status_label",
    "recurring_frequency_label",
    "recurring_occurrence_state_label",
    "recurring_template_status_label",
    "work_category_label",
    "work_contribution_role_label",
    "work_event_label",
    "work_exclusion_kind_label",
    "work_result_source_label",
    "work_result_status_label",
    "work_source_label",
    "work_status_label",
    "work_unit_label",
]
