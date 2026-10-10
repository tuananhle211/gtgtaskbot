"""Units and their members.

Two tables, added by ``0042`` beside the PR schema without touching it:

* ``org_units`` - the registry. Two rows (``PR``, ``ADS``), seeded by the
  migration; a unit's tunables live in its ``settings`` JSON and are edited on
  the admin page, never in deployment configuration.
* ``org_unit_members`` - one row per (unit, person), **kept for ever**. Leaving
  a unit sets ``left_at``; coming back clears it. The row is the tag that
  decides what a person may see, so deleting it would erase why somebody
  could read an order last month.
* ``unit_video_kinds`` (``0044``) - the unit's catalogue of video kinds
  ("Loại video") and the points each is worth. Never deleted: a kind that is
  no longer offered is deactivated, and orders keep a snapshot of its name
  and points, so editing the catalogue never rewrites an old order.
* ``unit_platforms`` (``0049``) - the unit's catalogue of target platforms
  ("Nền tảng": TikTok, Facebook, YouTube…). No points.
* ``unit_durations`` (``0049``) - the unit's catalogue of video durations
  ("Thời lượng": 30s, 1p, 1p30s…) with configurable points.

What is deliberately **not** here: no hierarchy, no manager link, no team
under a unit. The Work ledger and the capability grants still infer nothing
organisational - a unit is a wall, not an org chart.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from meobot.db.base import Base, JSONColumn, TimestampMixin, UUIDPrimaryKeyMixin, value_enum
from meobot.db.models.pr import RESTRICT, USERS_TABLE
from meobot.domain.units.models import UnitCode, UnitMemberRole

ORG_UNITS = "org_units"
ORG_UNIT_MEMBERS = "org_unit_members"
UNIT_VIDEO_KINDS = "unit_video_kinds"
UNIT_PLATFORMS = "unit_platforms"
UNIT_DURATIONS = "unit_durations"

#: A tag is active while it has not been closed.
ACTIVE_MEMBERSHIP = text("left_at IS NULL")


def _not_empty(column: str) -> str:
    return f"length(trim({column})) > 0"


class OrgUnit(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One department. ``code`` is the identifier code branches on."""

    __tablename__ = ORG_UNITS
    __table_args__ = (CheckConstraint(_not_empty("name"), name="name_not_empty"),)

    code: Mapped[UnitCode] = mapped_column(
        value_enum(UnitCode, name="org_unit_code", length=10), nullable=False, unique=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    #: :class:`~meobot.domain.units.models.UnitSettings`, validated on read so
    #: an old row keeps working when a setting is added.
    settings: Mapped[dict[str, Any]] = mapped_column(
        JSONColumn, nullable=False, default=dict, server_default=text("'{}'")
    )
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )


class OrgUnitMember(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One person's tag in one unit.

    ``member_code`` is the short upper-case handle that opens an order code
    (``TUAN`` in ``TUAN-D-261003-01``); the department head hands it out, or
    the first order derives it from the name. Not unique since 0052 (rules for
    codes come later). PR members have none.
    """

    __tablename__ = ORG_UNIT_MEMBERS
    __table_args__ = (
        UniqueConstraint("unit_id", "user_id", name="uq_org_unit_members_unit_id_user_id"),
        CheckConstraint(
            "member_code IS NULL OR length(trim(member_code)) > 0", name="member_code_not_blank"
        ),
        Index("ix_org_unit_members_user_id", "user_id"),
    )

    unit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{ORG_UNITS}.id", ondelete=RESTRICT), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=False
    )
    role: Mapped[UnitMemberRole] = mapped_column(
        value_enum(UnitMemberRole, name="org_unit_member_role", length=20), nullable=False
    )
    #: The Leader of a production function: assigns work and approves it.
    is_lead: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    member_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    personal_nas_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: ORD (0050): the one Leader (an orderer: the one head) this member reports
    #: to. Their hand-ins and orders wait on that person only; none = every
    #: Leader (head) of the ban.
    manager_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )
    #: ORD (0053): this member's own daily token budget; NULL = the unit's
    #: ``UnitSettings.default_daily_tokens``.
    daily_tokens: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def is_active(self) -> bool:
        return self.left_at is None


class UnitVideoKind(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One video kind a unit offers on its order form ("Loại video")."""

    __tablename__ = UNIT_VIDEO_KINDS
    __table_args__ = (
        CheckConstraint(_not_empty("name"), name="name_not_empty"),
        CheckConstraint("points >= 0", name="points_not_negative"),
        # One name per unit, whatever the case: "Short video" and
        # "short video" are the same kind to a person picking from a list.
        Index("uq_unit_video_kinds_unit_name", "unit_id", text("lower(name)"), unique=True),
    )

    unit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{ORG_UNITS}.id", ondelete=RESTRICT), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    points: Mapped[Decimal] = mapped_column(
        Numeric(6, 2), nullable=False, default=Decimal("1"), server_default=text("1")
    )
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )


class UnitPlatform(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One target platform a unit offers on its order form ("Nền tảng")."""

    __tablename__ = UNIT_PLATFORMS
    __table_args__ = (
        CheckConstraint(_not_empty("name"), name="name_not_empty"),
        Index("uq_unit_platforms_unit_name", "unit_id", text("lower(name)"), unique=True),
    )

    unit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{ORG_UNITS}.id", ondelete=RESTRICT), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )


class UnitDuration(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One video duration a unit offers on its order form ("Thời lượng")."""

    __tablename__ = UNIT_DURATIONS
    __table_args__ = (
        CheckConstraint(_not_empty("name"), name="name_not_empty"),
        CheckConstraint("points >= 0", name="points_not_negative"),
        Index("uq_unit_durations_unit_name", "unit_id", text("lower(name)"), unique=True),
    )

    unit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{ORG_UNITS}.id", ondelete=RESTRICT), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    points: Mapped[Decimal] = mapped_column(
        Numeric(6, 2), nullable=False, default=Decimal("1"), server_default=text("1")
    )
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )


__all__ = [
    "ACTIVE_MEMBERSHIP",
    "ORG_UNITS",
    "ORG_UNIT_MEMBERS",
    "UNIT_DURATIONS",
    "UNIT_PLATFORMS",
    "UNIT_VIDEO_KINDS",
    "OrgUnit",
    "OrgUnitMember",
    "UnitDuration",
    "UnitPlatform",
    "UnitVideoKind",
]
