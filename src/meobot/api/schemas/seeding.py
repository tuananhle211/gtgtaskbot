"""Payloads of the seeding module (0085)."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field

from meobot.application.seeding.service import (
    LineResult,
    MemberFigures,
    OtherView,
    Overview,
    Period,
    Person,
    PhoneView,
    SeedingAccess,
)
from meobot.domain.performance.work_points import PHONE_POINTS


class SeedingAccessResponse(BaseModel):
    #: In the seeding group: sees the menu, hands numbers in.
    member: bool = False
    #: A Trưởng phòng PR / the OWNER / an ADMIN: people, KPI, decisions.
    manage: bool = False

    @classmethod
    def from_access(cls, access: SeedingAccess) -> SeedingAccessResponse:
        return cls(member=access.member, manage=access.manage)


class SeedingPersonResponse(BaseModel):
    user_id: uuid.UUID
    name: str
    departments: list[str]

    @classmethod
    def from_domain(cls, person: Person) -> SeedingPersonResponse:
        return cls(user_id=person.user_id, name=person.name, departments=list(person.departments))


class SeedingPeriodResponse(BaseModel):
    kind: str
    start: date
    end: date
    target: int | None
    approved: int
    pending: int
    rate: float | None

    @classmethod
    def from_domain(cls, period: Period) -> SeedingPeriodResponse:
        return cls(
            kind=period.kind,
            start=period.start,
            end=period.end,
            target=period.target,
            approved=period.approved,
            pending=period.pending,
            rate=period.rate,
        )


class SeedingMemberRowResponse(BaseModel):
    person: SeedingPersonResponse
    approved_week: int
    approved_month: int
    pending: int
    phone_points_month: int
    other_points_month: int

    @classmethod
    def from_domain(cls, row: MemberFigures) -> SeedingMemberRowResponse:
        return cls(
            person=SeedingPersonResponse.from_domain(row.person),
            approved_week=row.approved_week,
            approved_month=row.approved_month,
            pending=row.pending,
            phone_points_month=row.phone_points_month,
            other_points_month=row.other_points_month,
        )


class SeedingOverviewResponse(BaseModel):
    day: date
    week: SeedingPeriodResponse
    month: SeedingPeriodResponse
    members: list[SeedingMemberRowResponse]
    phone_points: int = PHONE_POINTS
    access: SeedingAccessResponse

    @classmethod
    def from_domain(cls, view: Overview, access: SeedingAccess) -> SeedingOverviewResponse:
        return cls(
            day=view.day,
            week=SeedingPeriodResponse.from_domain(view.week),
            month=SeedingPeriodResponse.from_domain(view.month),
            members=[SeedingMemberRowResponse.from_domain(row) for row in view.members],
            access=SeedingAccessResponse.from_access(access),
        )


class SeedingTargetRequest(BaseModel):
    #: WEEK / MONTH.
    kind: str = Field(max_length=6)
    #: Any day of the week / month.
    day: date
    #: ``None`` clears it.
    target: int | None = Field(default=None, ge=0, le=1_000_000)


class SeedingMemberRequest(BaseModel):
    user_id: uuid.UUID


class SeedingPhonesRequest(BaseModel):
    #: One number per line.
    text: str = Field(max_length=100_000)


class SeedingLineResponse(BaseModel):
    number: int
    raw: str
    phone: str | None
    #: NEW / DUPLICATE / REPEATED / INVALID.
    status: str

    @classmethod
    def from_domain(cls, line: LineResult) -> SeedingLineResponse:
        return cls(number=line.number, raw=line.raw, phone=line.phone, status=line.status)


class SeedingSubmitResponse(BaseModel):
    accepted: int
    lines: list[SeedingLineResponse]


class SeedingPhoneResponse(BaseModel):
    id: uuid.UUID
    phone: str
    user_id: uuid.UUID
    name: str
    submitted_at: datetime
    status: str
    decided_by: str | None
    decided_at: datetime | None
    decision_note: str | None

    @classmethod
    def from_domain(cls, view: PhoneView) -> SeedingPhoneResponse:
        row = view.row
        return cls(
            id=row.id,
            phone=row.phone,
            user_id=row.submitted_by_user_id,
            name=view.name,
            submitted_at=row.submitted_at,
            status=row.status,
            decided_by=view.decided_by,
            decided_at=row.decided_at,
            decision_note=row.decision_note,
        )


class SeedingOtherRequest(BaseModel):
    description: str = Field(max_length=2000)
    points: int = Field(ge=1, le=100_000)


class SeedingOtherResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    description: str
    points: int
    submitted_at: datetime
    status: str
    decided_by: str | None
    decided_at: datetime | None
    decision_note: str | None

    @classmethod
    def from_domain(cls, view: OtherView) -> SeedingOtherResponse:
        row = view.row
        return cls(
            id=row.id,
            user_id=row.user_id,
            name=view.name,
            description=row.description,
            points=row.points,
            submitted_at=row.submitted_at,
            status=row.status,
            decided_by=view.decided_by,
            decided_at=row.decided_at,
            decision_note=row.decision_note,
        )


class SeedingDecisionRequest(BaseModel):
    ids: list[uuid.UUID] = Field(max_length=1000)
    approve: bool
    note: str | None = Field(default=None, max_length=1000)


class SeedingDecisionResponse(BaseModel):
    decided: int
