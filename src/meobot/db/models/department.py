"""Departments ("Phòng ban"), who sits in them, and what they grant.

See :mod:`meobot.domain.org.models`. The order flow's stream tags
(``org_unit_members`` in the ``ADS`` and ``PR`` units) are no longer edited
by hand: they are derived from these rows
(:func:`meobot.application.org.departments.sync_stream_tags`).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from meobot.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, value_enum
from meobot.db.models.pr import RESTRICT, USERS_TABLE
from meobot.domain.org.models import DepartmentFunction, Position

DEPARTMENTS = "departments"
DEPARTMENT_MEMBERS = "department_members"
DEPARTMENT_PERMISSIONS = "department_permissions"

#: A seat is open while it has not been closed.
OPEN_SEAT = text("left_at IS NULL")


class Department(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = DEPARTMENTS
    __table_args__ = (
        CheckConstraint("length(trim(code)) > 0", name="code_not_empty"),
        CheckConstraint("length(trim(name)) > 0", name="name_not_empty"),
    )

    #: Short and stable: it prefixes codes elsewhere (``ADS-001``).
    code: Mapped[str] = mapped_column(String(12), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    function: Mapped[DepartmentFunction] = mapped_column(
        value_enum(DepartmentFunction, name="department_function", length=20),
        nullable=False,
        default=DepartmentFunction.NONE,
        server_default=DepartmentFunction.NONE.value,
    )
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )


class DepartmentMember(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One person's seat in one department. Closed, never deleted."""

    __tablename__ = DEPARTMENT_MEMBERS
    __table_args__ = (
        # One open seat per person and department.
        Index(
            "uq_department_members_open_seat",
            "department_id",
            "user_id",
            unique=True,
            postgresql_where=OPEN_SEAT,
            sqlite_where=OPEN_SEAT,
        ),
        Index("ix_department_members_user_id", "user_id"),
        Index("ix_department_members_manager_user_id", "manager_user_id"),
    )

    department_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{DEPARTMENTS}.id", ondelete=RESTRICT), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=False
    )
    position: Mapped[Position] = mapped_column(
        value_enum(Position, name="department_position", length=10),
        nullable=False,
        default=Position.MEMBER,
        server_default=Position.MEMBER.value,
    )
    #: A head of the same department this person reports to.
    manager_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DepartmentPermission(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One cell of a department's grants: module, permission and position."""

    __tablename__ = DEPARTMENT_PERMISSIONS
    __table_args__ = (
        UniqueConstraint(
            "department_id",
            "position",
            "module",
            "permission",
            name="uq_department_permissions_cell",
        ),
    )

    department_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{DEPARTMENTS}.id", ondelete=RESTRICT), nullable=False, index=True
    )
    position: Mapped[Position] = mapped_column(
        value_enum(Position, name="department_position", length=10), nullable=False
    )
    module: Mapped[str] = mapped_column(String(20), nullable=False)
    permission: Mapped[str] = mapped_column(String(40), nullable=False)
    #: ``OWN`` / ``ALL`` (``NONE`` is not stored).
    scope: Mapped[str] = mapped_column(String(10), nullable=False)


__all__ = [
    "DEPARTMENTS",
    "DEPARTMENT_MEMBERS",
    "DEPARTMENT_PERMISSIONS",
    "Department",
    "DepartmentMember",
    "DepartmentPermission",
]
