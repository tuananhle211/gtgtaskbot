"""Points earned outside the order flow, and the PR / seeding records behind
them (0085). See :mod:`meobot.domain.performance.work_points`.

* ``work_points`` - the ledger the reports read beside ``order_node_points``;
* ``pr_point_claims`` - one person's task codes for one stage of one PR piece,
  waiting for / approved by a Trưởng phòng PR;
* ``seeding_members`` - who is in the PR seeding group;
* ``seeding_targets`` - the team's phone KPI per week / month;
* ``seeding_phones`` - every number handed in (never twice while not refused);
* ``seeding_other_reports`` - "Việc khác" a seeding member reported.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from meobot.core.time import utcnow
from meobot.db.base import Base, JSONColumn, UUIDPrimaryKeyMixin
from meobot.db.models.department import DEPARTMENTS
from meobot.db.models.pr import RESTRICT, USERS_TABLE

WORK_POINTS = "work_points"
PR_POINT_CLAIMS = "pr_point_claims"
SEEDING_MEMBERS = "seeding_members"
SEEDING_TARGETS = "seeding_targets"
SEEDING_PHONES = "seeding_phones"
SEEDING_OTHER_REPORTS = "seeding_other_reports"
PR_CONTENT_ITEMS = "pr_content_items"

#: Decisions shared by the claims, phones and "việc khác" reports.
DECISION_STATES = "('PENDING', 'APPROVED', 'REJECTED')"


def _user(nullable: bool = False) -> Mapped[Any]:
    return mapped_column(ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=nullable)


class WorkPoint(Base, UUIDPrimaryKeyMixin):
    __tablename__ = WORK_POINTS
    __table_args__ = (
        Index("ix_work_points_user_earned", "user_id", "earned_at"),
        Index("ix_work_points_department_earned", "department_id", "earned_at"),
    )

    #: ``WorkPointSource``.
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    #: What wrote it, e.g. ``pr_claim:<id>:THIET_KE`` - rewritten, never doubled.
    source_key: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    user_id: Mapped[uuid.UUID] = _user()
    department_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{DEPARTMENTS}.id", ondelete=RESTRICT), nullable=True
    )
    #: BIEN_TAP / THIET_KE / DUNG / PUBLISH / SEEDING / OTHER.
    phase: Mapped[str] = mapped_column(String(20), nullable=False)
    items: Mapped[list[dict[str, Any]]] = mapped_column(JSONColumn, nullable=False, default=list)
    #: Before the person's coefficient.
    points: Mapped[int] = mapped_column(Integer, nullable=False)
    #: When it counts (its month).
    earned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: What it is about, for the lines: a PR piece's code / title, a link.
    ref_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ref_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    ref_link: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )


class PrPointClaim(Base, UUIDPrimaryKeyMixin):
    __tablename__ = PR_POINT_CLAIMS
    __table_args__ = (
        UniqueConstraint("content_id", "stage", "user_id", name="uq_pr_point_claims_person"),
        CheckConstraint("stage IN ('CONTENT', 'PRODUCTION')", name="stage_known"),
        CheckConstraint(f"status IN {DECISION_STATES}", name="status_known"),
        Index("ix_pr_point_claims_status", "status"),
    )

    #: CASCADE: a PR piece deleted for good takes its claims with it.
    content_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{PR_CONTENT_ITEMS}.id", ondelete="CASCADE"), nullable=False
    )
    stage: Mapped[str] = mapped_column(String(12), nullable=False)
    user_id: Mapped[uuid.UUID] = _user()
    #: ``{"BIEN_TAP": [item, …]}`` / ``{"THIET_KE": […], "DUNG": […]}``.
    items: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)
    points: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="PENDING")
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_by_user_id: Mapped[uuid.UUID | None] = _user(nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: On a refusal: CHANGED_REQUEST / WRONG_CODES / WORKER_FAULT.
    return_reason: Mapped[str | None] = mapped_column(String(20), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class SeedingMember(Base, UUIDPrimaryKeyMixin):
    __tablename__ = SEEDING_MEMBERS
    __table_args__ = (
        Index(
            "uq_seeding_members_open",
            "user_id",
            unique=True,
            postgresql_where=text("removed_at IS NULL"),
            sqlite_where=text("removed_at IS NULL"),
        ),
    )

    user_id: Mapped[uuid.UUID] = _user()
    added_by_user_id: Mapped[uuid.UUID | None] = _user(nullable=True)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    removed_by_user_id: Mapped[uuid.UUID | None] = _user(nullable=True)


class SeedingTarget(Base, UUIDPrimaryKeyMixin):
    __tablename__ = SEEDING_TARGETS
    __table_args__ = (
        UniqueConstraint("kind", "period_start", name="uq_seeding_targets_period"),
        CheckConstraint("kind IN ('WEEK', 'MONTH')", name="kind_known"),
        CheckConstraint("target >= 0", name="target_not_negative"),
    )

    #: WEEK (Monday-Sunday) / MONTH.
    kind: Mapped[str] = mapped_column(String(6), nullable=False)
    #: The Monday / the 1st.
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    #: Phone numbers the team is to hand in.
    target: Mapped[int] = mapped_column(Integer, nullable=False)
    set_by_user_id: Mapped[uuid.UUID | None] = _user(nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )


class SeedingPhone(Base, UUIDPrimaryKeyMixin):
    __tablename__ = SEEDING_PHONES
    __table_args__ = (
        Index(
            "uq_seeding_phones_live",
            "phone",
            unique=True,
            postgresql_where=text("status <> 'REJECTED'"),
            sqlite_where=text("status <> 'REJECTED'"),
        ),
        Index("ix_seeding_phones_person", "submitted_by_user_id", "submitted_at"),
        Index("ix_seeding_phones_status", "status"),
        CheckConstraint(f"status IN {DECISION_STATES}", name="status_known"),
    )

    #: Normalised: digits only, starting with 0.
    phone: Mapped[str] = mapped_column(String(20), nullable=False)
    submitted_by_user_id: Mapped[uuid.UUID] = _user()
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="PENDING")
    decided_by_user_id: Mapped[uuid.UUID | None] = _user(nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class SeedingOtherReport(Base, UUIDPrimaryKeyMixin):
    __tablename__ = SEEDING_OTHER_REPORTS
    __table_args__ = (
        CheckConstraint("points > 0", name="points_positive"),
        CheckConstraint(f"status IN {DECISION_STATES}", name="status_known"),
        Index("ix_seeding_other_reports_person", "user_id", "submitted_at"),
    )

    user_id: Mapped[uuid.UUID] = _user()
    description: Mapped[str] = mapped_column(Text, nullable=False)
    points: Mapped[int] = mapped_column(Integer, nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="PENDING")
    decided_by_user_id: Mapped[uuid.UUID | None] = _user(nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)


__all__ = [
    "PR_POINT_CLAIMS",
    "SEEDING_MEMBERS",
    "SEEDING_OTHER_REPORTS",
    "SEEDING_PHONES",
    "SEEDING_TARGETS",
    "WORK_POINTS",
    "PrPointClaim",
    "SeedingMember",
    "SeedingOtherReport",
    "SeedingPhone",
    "SeedingTarget",
    "WorkPoint",
]
