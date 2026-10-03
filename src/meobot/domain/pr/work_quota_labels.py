"""M2's vocabulary in Vietnamese. One wording per value, in one place.

Separate from :mod:`meobot.domain.pr.work_labels` for the same reason that file
is separate from the content module's: M2 is a distinct milestone with its own
readers, and the two tables share no value.

Every label here is sent to the client on a ``*_label`` field, so the browser
holds no second copy and a Telegram tool, a web panel and a future export all
say the same word for the same state.

Three wordings are load-bearing
--------------------------------

``ELIGIBLE`` is **"Đủ điều kiện tính KPI"** and deliberately **not** "Đã được
tính điểm". Nothing has been scored: M2 decides whether counted work sits
inside an approved quota, and a label promising points would be describing a
feature that does not exist. Points are M6's.

``NO_QUOTA`` is **"Chưa có hạn mức KPI"** - *no cap has been set yet* - and not
"Không đạt" or "Ngoài kế hoạch". The work is real, valid and in the workload;
what is missing is a decision by somebody with the authority to make one, and
the sentence has to send the reader to that person rather than tell them their
work was rejected.

``UNMEASURABLE`` is **"Chưa thể tính hạn mức"** - *the cap cannot be applied
yet* - and it is a different sentence from the one above on purpose. There **is**
a target; what is missing is a number on the work item, and the reason line says
which one. It sends the reader to fix a row rather than to ask for a quota, and
it must never read as "your work does not count": the contribution is
``COUNTED`` and stays ``COUNTED``.

``OVER_QUOTA`` is **"Vượt hạn mức"**, which says a cap exists and this is past
it. That is a different sentence and a different management response from the
one above, which is why the two are different values.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from types import MappingProxyType

from meobot.domain.pr.work import PrWorkUnit
from meobot.domain.pr.work_labels import work_unit_label
from meobot.domain.pr.work_quota import (
    PrPlanReadinessBlocker,
    PrPlanReviewState,
    PrWorkPlanStatus,
    PrWorkQuotaBasis,
    PrWorkQuotaStatus,
    PrWorkUnmeasurableReason,
)

#: How a quota measures its work type.
WORK_QUOTA_BASIS_LABELS: Mapping[PrWorkQuotaBasis, str] = MappingProxyType(
    {
        PrWorkQuotaBasis.ITEM_COUNT: "Theo số đầu việc",
        PrWorkQuotaBasis.QUANTITY: "Theo số lượng",
    }
)

#: A short gloss for a picker, because "theo số đầu việc" and "theo số lượng"
#: are the same length and the difference is the whole decision.
WORK_QUOTA_BASIS_HINTS: Mapping[PrWorkQuotaBasis, str] = MappingProxyType(
    {
        PrWorkQuotaBasis.ITEM_COUNT: (
            "Mỗi đầu việc được ghi nhận tính là 1. Ví dụ: 20 kịch bản video ngắn."
        ),
        PrWorkQuotaBasis.QUANTITY: (
            "Tính theo số lượng ghi trên đầu việc. Ví dụ: 3.000 bình luận, "
            "trong đó một đầu việc có thể là 100 bình luận."
        ),
    }
)

WORK_PLAN_STATUS_LABELS: Mapping[PrWorkPlanStatus, str] = MappingProxyType(
    {
        PrWorkPlanStatus.DRAFT: "Bản nháp",
        PrWorkPlanStatus.APPROVED: "Đang áp dụng",
        PrWorkPlanStatus.SUPERSEDED: "Đã thay thế",
        PrWorkPlanStatus.DISCARDED: "Đã bỏ",
    }
)

#: KPI self-service. What a draft's working situation is called on a card -
#: the employee's and the manager's, so the two screens use one vocabulary.
PLAN_REVIEW_STATE_LABELS: Mapping[PrPlanReviewState, str] = MappingProxyType(
    {
        PrPlanReviewState.EDITING: "Bản nháp",
        PrPlanReviewState.SUBMITTED: "Chờ duyệt",
        PrPlanReviewState.RETURNED: "Cần chỉnh sửa",
    }
)

#: Why a draft cannot be sent or approved yet, as a sentence for the person
#: who has to fix it. Keyed on the stable code, never on the English message.
PLAN_READINESS_BLOCKER_LABELS: Mapping[PrPlanReadinessBlocker, str] = MappingProxyType(
    {
        PrPlanReadinessBlocker.PERIOD_NOT_OPEN: "Kỳ báo cáo đã đóng, không thể thay đổi kế hoạch.",
        PrPlanReadinessBlocker.SUBJECT_INACTIVE: "Nhân sự này không còn hoạt động.",
        PrPlanReadinessBlocker.PLAN_HAS_NO_QUOTAS: "Cần ít nhất một chỉ tiêu.",
        PrPlanReadinessBlocker.WORK_TYPE_INACTIVE: (
            "Có chỉ tiêu thuộc loại công việc không còn được áp dụng."
        ),
        PrPlanReadinessBlocker.QUOTA_UNIT_MISMATCH: (
            "Có chỉ tiêu tính theo đơn vị không khớp với loại công việc."
        ),
        PrPlanReadinessBlocker.QUOTA_BOUNDS_INVALID: (
            "Có chỉ tiêu với mục tiêu hoặc trần không hợp lệ."
        ),
    }
)


def plan_review_state_label(state: PrPlanReviewState) -> str:
    return PLAN_REVIEW_STATE_LABELS.get(state, state.value)


def plan_readiness_blocker_label(code: str) -> str:
    """The sentence for one readiness code; the code itself for an unknown one."""
    try:
        return PLAN_READINESS_BLOCKER_LABELS[PrPlanReadinessBlocker(code)]
    except ValueError:
        return code


#: The order a client offers plan statuses in. Presentation only.
WORK_PLAN_STATUS_ORDER: tuple[PrWorkPlanStatus, ...] = (
    PrWorkPlanStatus.DRAFT,
    PrWorkPlanStatus.APPROVED,
    PrWorkPlanStatus.SUPERSEDED,
    PrWorkPlanStatus.DISCARDED,
)

#: What an approved quota decided about one counted contribution.
WORK_QUOTA_STATUS_LABELS: Mapping[PrWorkQuotaStatus, str] = MappingProxyType(
    {
        # Not "Không đạt". Nobody set a cap - see the module docstring.
        PrWorkQuotaStatus.NO_QUOTA: "Chưa có hạn mức KPI",
        # There *is* a cap; a number on the work item is missing. Not "Không
        # hợp lệ" - the work is valid and counted.
        PrWorkQuotaStatus.UNMEASURABLE: "Chưa thể tính hạn mức",
        # Not "Đã được tính điểm". Nothing has been scored.
        PrWorkQuotaStatus.ELIGIBLE: "Đủ điều kiện tính KPI",
        PrWorkQuotaStatus.PARTIALLY_ELIGIBLE: "Đủ điều kiện một phần",
        PrWorkQuotaStatus.OVER_QUOTA: "Vượt hạn mức",
        # Read-only, and never on a stored row. "Chưa tính" rather than "Chưa có
        # hạn mức": a target exists and nothing has looked at this work yet.
        PrWorkQuotaStatus.PENDING_EVALUATION: "Chưa tính điều kiện KPI",
    }
)

#: The longer sentence a detail panel shows under the badge. Each one says what
#: is true of the work as well as what the status decided, because "vượt hạn
#: mức" on its own reads as a reprimand for work that was done correctly.
WORK_QUOTA_STATUS_HINTS: Mapping[PrWorkQuotaStatus, str] = MappingProxyType(
    {
        PrWorkQuotaStatus.NO_QUOTA: (
            "Đã ghi nhận công việc, chưa có hạn mức KPI cho loại việc này trong kỳ."
        ),
        PrWorkQuotaStatus.ELIGIBLE: "Nằm trong hạn mức KPI đã duyệt của kỳ này.",
        PrWorkQuotaStatus.PARTIALLY_ELIGIBLE: (
            "Một phần nằm trong hạn mức KPI đã duyệt, phần còn lại vượt hạn mức."
        ),
        PrWorkQuotaStatus.OVER_QUOTA: (
            "Công việc vẫn được ghi nhận đầy đủ, nhưng hạn mức KPI của kỳ đã dùng hết."
        ),
        PrWorkQuotaStatus.UNMEASURABLE: (
            "Công việc đã được ghi nhận. Hạn mức KPI đã có, nhưng còn thiếu dữ liệu để đối chiếu."
        ),
        PrWorkQuotaStatus.PENDING_EVALUATION: (
            "Công việc đã được ghi nhận. Hạn mức KPI đã có, nhưng kỳ này chưa được tính lại."
        ),
    }
)


#: Why a contribution could not be measured, in one short sentence each.
#:
#: One line, naming the field somebody has to fix. Deliberately **not** the
#: evaluator's own words: the code is stable and the sentence is not, and a
#: screen that printed an internal message would change what an employee reads
#: whenever a docstring was reworded.
WORK_UNMEASURABLE_REASON_LABELS: Mapping[PrWorkUnmeasurableReason, str] = MappingProxyType(
    {
        PrWorkUnmeasurableReason.MISSING_QUANTITY: "Thiếu số lượng công việc.",
        PrWorkUnmeasurableReason.INVALID_QUANTITY: "Số lượng công việc chưa hợp lệ.",
        PrWorkUnmeasurableReason.UNIT_MISMATCH: ("Đơn vị công việc không khớp hạn mức KPI."),
    }
)


#: What one unit of an ``ITEM_COUNT`` quota is called. A count of work items,
#: whatever each item delivers - "28 đầu việc", never "28 sản phẩm".
ITEM_COUNT_UNIT_LABEL = "đầu việc"


def quota_target_unit_label(basis: PrWorkQuotaBasis, unit: PrWorkUnit | None) -> str:
    """The word after a quota's target: *đầu việc* for a count, else the unit.

    One helper, because the same word must follow the target on a card, sit in
    the rule label ("30 phút / đầu việc") and head the config table - and a
    QUANTITY quota's unit is the work type's, never a free string.
    """
    if basis is PrWorkQuotaBasis.ITEM_COUNT or unit is None:
        return ITEM_COUNT_UNIT_LABEL
    return work_unit_label(unit)


def format_minutes(value: Decimal) -> str:
    """A minute figure as a person reads it: ``0.9`` → ``0,9``, ``30.00`` → ``30``.

    Trailing zeros go and the decimal separator is Vietnamese. Presentation
    only - nothing stores this string - and shared by every label below so the
    rule table and the KPI card print one number the same way.
    """
    text = format(value.normalize(), "f") if value == value.to_integral() else format(value, "f")
    text = text.rstrip("0").rstrip(".") if "." in text else text
    return text.replace(".", ",") or "0"


def workload_rule_label(
    standard_minutes_per_unit: Decimal, basis: PrWorkQuotaBasis, unit: PrWorkUnit | None
) -> str:
    """How a scoring rule reads on a card: ``"30 phút / đầu việc"``.

    Always *per one unit*, because that is what the rule model stores
    (``standard_minutes_per_unit``, four decimal places) and what M6 multiplies
    an eligible amount by. A rule expressed as "120 phút cho 120 bình luận" is
    entered as ``1`` and printed as "1 phút / bình luận"; the screen never
    invents a batch size the model does not hold.
    """
    return (
        f"{format_minutes(standard_minutes_per_unit)} phút / {quota_target_unit_label(basis, unit)}"
    )


#: Why a quota carries no workload minutes. Keyed on the M6 score status the
#: calculator reuses, so the KPI card and the performance screen agree.
QUOTA_WORKLOAD_STATUS_LABELS: Mapping[str, str] = MappingProxyType(
    {
        "PRICED": "Đã quy đổi",
        # M6's own word for the same fact, so a work card and a plan row read alike.
        "SCORED": "Đã quy đổi",
        "NO_SCORING_RULE": "Chưa cấu hình quy tắc workload",
        "EXCLUDED_FROM_PERFORMANCE": "Không tính vào workload",
    }
)


def quota_workload_status_label(status: str) -> str:
    return QUOTA_WORKLOAD_STATUS_LABELS.get(status, status)


#: Why a month's target minutes could not be resolved, for the person reading
#: the card. Keyed on ``TargetResolution.unresolved_reason``.
TARGET_UNRESOLVED_LABELS: Mapping[str, str] = MappingProxyType(
    {
        "no_active_work_schedule": "Chưa có lịch làm việc đang áp dụng cho kỳ này.",
        "work_schedule_has_no_working_days": "Lịch làm việc của kỳ này không có ngày làm việc.",
    }
)


def target_unresolved_label(reason: str | None) -> str | None:
    if reason is None:
        return None
    return TARGET_UNRESOLVED_LABELS.get(reason, "Chưa xác định được mục tiêu phút của kỳ.")


def work_quota_basis_label(basis: PrWorkQuotaBasis) -> str:
    """The Vietnamese name of a quota basis, or its code if somebody adds one."""
    return WORK_QUOTA_BASIS_LABELS.get(basis, basis.value)


def work_quota_basis_hint(basis: PrWorkQuotaBasis) -> str:
    return WORK_QUOTA_BASIS_HINTS.get(basis, "")


def work_plan_status_label(status: PrWorkPlanStatus) -> str:
    return WORK_PLAN_STATUS_LABELS.get(status, status.value)


def work_quota_status_label(status: PrWorkQuotaStatus) -> str:
    return WORK_QUOTA_STATUS_LABELS.get(status, status.value)


def work_quota_status_hint(status: PrWorkQuotaStatus) -> str:
    return WORK_QUOTA_STATUS_HINTS.get(status, "")


def work_unmeasurable_reason_label(reason: PrWorkUnmeasurableReason | None) -> str | None:
    """The sentence for one reason code, or ``None`` when there is no reason.

    ``None`` in and ``None`` out, so a caller can hand it any allocation's
    ``reason_code`` without branching - the field is null for every status but
    ``UNMEASURABLE``.
    """
    if reason is None:
        return None
    return WORK_UNMEASURABLE_REASON_LABELS.get(reason, reason.value)


__all__: list[str] = [
    "ITEM_COUNT_UNIT_LABEL",
    "PLAN_READINESS_BLOCKER_LABELS",
    "PLAN_REVIEW_STATE_LABELS",
    "QUOTA_WORKLOAD_STATUS_LABELS",
    "TARGET_UNRESOLVED_LABELS",
    "WORK_PLAN_STATUS_LABELS",
    "WORK_PLAN_STATUS_ORDER",
    "WORK_QUOTA_BASIS_HINTS",
    "WORK_QUOTA_BASIS_LABELS",
    "WORK_QUOTA_STATUS_HINTS",
    "WORK_QUOTA_STATUS_LABELS",
    "WORK_UNMEASURABLE_REASON_LABELS",
    "format_minutes",
    "plan_readiness_blocker_label",
    "plan_review_state_label",
    "quota_target_unit_label",
    "quota_workload_status_label",
    "target_unresolved_label",
    "work_plan_status_label",
    "work_quota_basis_hint",
    "work_quota_basis_label",
    "work_quota_status_hint",
    "work_quota_status_label",
    "work_unmeasurable_reason_label",
    "workload_rule_label",
]
