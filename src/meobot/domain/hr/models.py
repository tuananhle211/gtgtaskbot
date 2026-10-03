"""Leave and late-arrival requests: the vocabulary and the Vietnamese wording.

Internal names stay English because they are stored, queried and audited. Every
word a person reads is Vietnamese, and the mapping lives here so a status
cannot be spelled one way on a card and another way in a summary.

The type distinction matters for arithmetic, not just for display: a morning
leave is half a working day, an hourly leave is however many hours it covers,
and a late arrival is a number of minutes measured against a configured
work-start time. :mod:`~meobot.domain.hr.schedule` does that maths; this module
only names things.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class HrRequestType(StrEnum):
    """What is being asked for."""

    FULL_DAY_LEAVE = "FULL_DAY_LEAVE"
    MORNING_LEAVE = "MORNING_LEAVE"
    AFTERNOON_LEAVE = "AFTERNOON_LEAVE"
    HOURLY_LEAVE = "HOURLY_LEAVE"
    MULTI_DAY_LEAVE = "MULTI_DAY_LEAVE"
    LATE_ARRIVAL = "LATE_ARRIVAL"

    @property
    def is_leave(self) -> bool:
        return self is not HrRequestType.LATE_ARRIVAL


class HrRequestStatus(StrEnum):
    """Where a request has got to."""

    DRAFT = "DRAFT"
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    WITHDRAWN = "WITHDRAWN"
    EXPIRED = "EXPIRED"
    CHANGE_REQUESTED = "CHANGE_REQUESTED"

    @property
    def is_open(self) -> bool:
        """True while the request still needs somebody to do something."""
        return self in {
            HrRequestStatus.DRAFT,
            HrRequestStatus.PENDING,
            HrRequestStatus.CHANGE_REQUESTED,
        }

    @property
    def is_final(self) -> bool:
        """True once the request can no longer change on its own."""
        return not self.is_open


class HrEventType(StrEnum):
    """One line of a request's history."""

    CREATED = "CREATED"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CHANGE_REQUESTED = "CHANGE_REQUESTED"
    AMENDED = "AMENDED"
    WITHDRAWN = "WITHDRAWN"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


#: Vietnamese wording for a request type.
TYPE_LABELS: dict[HrRequestType, str] = {
    HrRequestType.FULL_DAY_LEAVE: "Nghỉ cả ngày",
    HrRequestType.MORNING_LEAVE: "Nghỉ buổi sáng",
    HrRequestType.AFTERNOON_LEAVE: "Nghỉ buổi chiều",
    HrRequestType.HOURLY_LEAVE: "Nghỉ theo giờ",
    HrRequestType.MULTI_DAY_LEAVE: "Nghỉ nhiều ngày",
    HrRequestType.LATE_ARRIVAL: "Đi muộn",
}

#: Vietnamese wording for a status. A raw enum value must never reach a person.
STATUS_LABELS: dict[HrRequestStatus, str] = {
    HrRequestStatus.DRAFT: "Chưa gửi",
    HrRequestStatus.PENDING: "Chờ duyệt",
    HrRequestStatus.APPROVED: "Đã duyệt",
    HrRequestStatus.REJECTED: "Bị từ chối",
    HrRequestStatus.CANCELLED: "Đã huỷ",
    HrRequestStatus.WITHDRAWN: "Đã rút yêu cầu",
    HrRequestStatus.EXPIRED: "Đã hết hiệu lực",
    HrRequestStatus.CHANGE_REQUESTED: "Cần bổ sung thông tin",
}


def type_label(request_type: HrRequestType) -> str:
    """Vietnamese name of a request type."""
    return TYPE_LABELS[request_type]


def status_label(status: HrRequestStatus) -> str:
    """Vietnamese name of a status."""
    return STATUS_LABELS[status]


class HrRequestDraft(BaseModel):
    """A request being assembled across several conversational turns.

    Kept as a value object so the flow can be stored, restored after a restart,
    previewed and validated without a database row existing yet - a request only
    becomes a row when the person confirms it.
    """

    model_config = ConfigDict(frozen=True)

    request_type: HrRequestType | None = None
    work_date: date | None = None
    end_date: date | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    expected_arrival_at: datetime | None = None
    late_minutes: int | None = Field(default=None, ge=0)
    reason: str = Field(default="", max_length=500)

    @property
    def needs_period(self) -> bool:
        """True while nobody has said morning / afternoon / all day / hours."""
        return self.request_type is None

    @property
    def needs_arrival(self) -> bool:
        """True for a late request with no arrival time and no duration yet."""
        return (
            self.request_type is HrRequestType.LATE_ARRIVAL
            and self.expected_arrival_at is None
            and self.late_minutes is None
        )

    @property
    def needs_date(self) -> bool:
        return self.work_date is None

    def missing_field(self, *, reason_required: bool) -> str | None:
        """The **one** thing to ask about next, or ``None`` when complete.

        Returning a single field is the mechanism behind the one-question rule:
        a caller cannot accidentally ask for four things at once because this
        never tells it about more than one.
        """
        if self.needs_date:
            return "work_date"
        if self.needs_period:
            return "period"
        if self.needs_arrival:
            return "arrival"
        if reason_required and not self.reason.strip():
            return "reason"
        return None


class LeaveTotals(BaseModel):
    """Deterministic leave totals for one person or one department.

    Every field is computed from stored rows by
    :class:`~meobot.application.hr_statistics_service.HrStatisticsService`. No
    model is involved in producing a number here - an LLM may narrate these
    totals afterwards, never calculate them.
    """

    model_config = ConfigDict(frozen=True)

    approved_count: int = 0
    pending_count: int = 0
    rejected_count: int = 0
    #: Working days, so a morning off counts 0.5.
    approved_days: float = 0.0


class LateTotals(BaseModel):
    """Deterministic late-arrival totals."""

    model_config = ConfigDict(frozen=True)

    approved_count: int = 0
    pending_count: int = 0
    rejected_count: int = 0
    total_minutes: int = 0

    @property
    def average_minutes(self) -> float:
        """Mean lateness across approved requests, 0 when there are none."""
        if self.approved_count == 0:
            return 0.0
        return round(self.total_minutes / self.approved_count, 1)


def format_days(days: float) -> str:
    """Vietnamese number formatting: a comma decimal separator, no trailing .0."""
    if days == int(days):
        return str(int(days))
    return f"{days:.1f}".replace(".", ",")


def overlaps(
    first_start: datetime, first_end: datetime, second_start: datetime, second_end: datetime
) -> bool:
    """True when two half-open intervals share any time.

    Half-open on purpose: a morning leave ending at 12:00 and an afternoon leave
    starting at 12:00 do not overlap, which is exactly the pair somebody files
    when they need a whole day but pick the two halves separately.
    """
    return first_start < second_end and second_start < first_end


def duration_days(request_type: HrRequestType, start_at: datetime, end_at: datetime) -> float:
    """Working-day weight of one leave request.

    Half-days are the common case and are worth stating explicitly rather than
    deriving from hours, because a "morning" is half a day whether the office
    opens at 08:00 or 08:30.
    """
    if request_type is HrRequestType.FULL_DAY_LEAVE:
        return 1.0
    if request_type in {HrRequestType.MORNING_LEAVE, HrRequestType.AFTERNOON_LEAVE}:
        return 0.5
    if request_type is HrRequestType.MULTI_DAY_LEAVE:
        return float((end_at.date() - start_at.date()).days + 1)
    if request_type is HrRequestType.HOURLY_LEAVE:
        hours = (end_at - start_at) / timedelta(hours=1)
        # Eight working hours to a day; rounded so a report shows "1,5" and not
        # a number with six decimal places.
        return round(hours / 8.0, 2)
    return 0.0
