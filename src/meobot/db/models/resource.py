"""Resources ("Quản lý tài nguyên", 0065): the inventory and its histories.

* ``resource_options`` - the pick-lists (type, role, status, origin,
  two-factor, handover), kept by the Phòng Tài nguyên;
* ``resources`` - one account / page / group / tool / ad account / BM;
* ``resource_code_counters`` - the last number handed out per prefix
  (``ADS-001``: one counter per department code);
* ``resource_handovers``, ``resource_status_history``, ``resource_changes`` -
  **append-only**: who held it and when, which status it had and when, and
  every field change (who, when, old → new). Database triggers refuse every
  ``UPDATE`` and ``DELETE`` on the three, on PostgreSQL and on the offline
  SQLite the unit suite runs on, so no code path - not even a hand-run
  statement - rewrites history (rule 4 of the requirement).

Nothing here holds a password, a 2FA secret or a recovery code: there is no
column for one (rule 1). ``external_id`` and ``phone`` are text so a long id
and a leading zero survive (rule 2).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import cast

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    event,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import DDL

from meobot.core.time import utcnow
from meobot.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, value_enum
from meobot.db.models.department import DEPARTMENTS
from meobot.db.models.pr import RESTRICT, USERS_TABLE
from meobot.domain.resources.models import HandoverReason, OptionKind, StatusGroup
from meobot.domain.resources.requests import RequestEventKind, RequestSite, RequestStage

RESOURCE_OPTIONS = "resource_options"
RESOURCES = "resources"
RESOURCE_CODE_COUNTERS = "resource_code_counters"
RESOURCE_HANDOVERS = "resource_handovers"
RESOURCE_STATUS_HISTORY = "resource_status_history"
RESOURCE_CHANGES = "resource_changes"
RESOURCE_REQUESTS = "resource_requests"
RESOURCE_REQUEST_EVENTS = "resource_request_events"

#: The tables no row of which is ever updated or deleted.
APPEND_ONLY_TABLES = (RESOURCE_HANDOVERS, RESOURCE_STATUS_HISTORY, RESOURCE_CHANGES)


def _not_empty(column: str) -> str:
    return f"length(trim({column})) > 0"


class ResourceOption(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One entry of one pick-list. ``code`` is stable (the import and the
    reports match on it); ``name`` is what people read and may rename.
    Never deleted: made inactive."""

    __tablename__ = RESOURCE_OPTIONS
    __table_args__ = (
        UniqueConstraint("kind", "code", name="uq_resource_options_kind_code"),
        CheckConstraint(_not_empty("code"), name="code_not_empty"),
        CheckConstraint(_not_empty("name"), name="name_not_empty"),
    )

    kind: Mapped[OptionKind] = mapped_column(
        value_enum(OptionKind, name="resource_option_kind", length=20), nullable=False
    )
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    #: STATUS entries only: what the status means to the reports.
    status_group: Mapped[StatusGroup | None] = mapped_column(
        value_enum(StatusGroup, name="resource_status_group", length=20), nullable=True
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )


class Resource(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = RESOURCES
    __table_args__ = (
        #: Rule 3: no two resources of one type share an id.
        UniqueConstraint("type_id", "external_id", name="uq_resources_type_id_external_id"),
        CheckConstraint(_not_empty("code"), name="code_not_empty"),
        CheckConstraint(_not_empty("external_id"), name="external_id_not_empty"),
        CheckConstraint(_not_empty("name"), name="name_not_empty"),
        Index("ix_resources_department_id", "department_id"),
        Index("ix_resources_holder_user_id", "holder_user_id"),
        Index("ix_resources_status_id", "status_id"),
    )

    #: ``ADS-001``: allocated once, kept when the resource changes department.
    code: Mapped[str] = mapped_column(String(24), nullable=False, unique=True)
    #: "Ban quản lý".
    department_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{DEPARTMENTS}.id", ondelete=RESTRICT), nullable=False
    )
    type_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{RESOURCE_OPTIONS}.id", ondelete=RESTRICT), nullable=False
    )
    #: "ID tài nguyên": text, so ``104000000000000`` and ``0123`` stay as typed.
    external_id: Mapped[str] = mapped_column(String(200), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    link: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: "Người được bàn giao". ``NULL`` = held by the Phòng Tài nguyên.
    holder_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )
    role_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{RESOURCE_OPTIONS}.id", ondelete=RESTRICT), nullable=True
    )
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: Text, for the leading zero.
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    two_factor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{RESOURCE_OPTIONS}.id", ondelete=RESTRICT), nullable=True
    )
    #: Who keeps the 2FA device - never the code itself.
    two_factor_holder_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )
    origin_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{RESOURCE_OPTIONS}.id", ondelete=RESTRICT), nullable=True
    )
    started_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    purpose: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: "Bàn giao về Tài nguyên".
    handover_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{RESOURCE_OPTIONS}.id", ondelete=RESTRICT), nullable=False
    )
    status_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{RESOURCE_OPTIONS}.id", ondelete=RESTRICT), nullable=False
    )
    #: "Ngày rà soát gần nhất": set by every status update and every review.
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )
    #: The "Đề xuất tài nguyên" this resource was bought for (0070).
    request_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{RESOURCE_REQUESTS}.id", ondelete=RESTRICT), nullable=True, index=True
    )


class ResourceRequest(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One "Đề xuất tài nguyên" (0070). Its stages: :class:`RequestStage`."""

    __tablename__ = RESOURCE_REQUESTS
    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("budget >= 0", name="budget_not_negative"),
    )

    code: Mapped[str] = mapped_column(String(16), nullable=False, unique=True)
    requester_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=False, index=True
    )
    type_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{RESOURCE_OPTIONS}.id", ondelete=RESTRICT), nullable=False
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    site: Mapped[RequestSite] = mapped_column(
        value_enum(RequestSite, name="resource_request_site", length=12), nullable=False
    )
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    requirements: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    replaces: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: "Dự toán tối đa", in đồng.
    budget: Mapped[int] = mapped_column(BigInteger, nullable=False)
    needed_by: Mapped[date] = mapped_column(Date, nullable=False)
    stage: Mapped[RequestStage] = mapped_column(
        value_enum(RequestStage, name="resource_request_stage", length=20), nullable=False
    )
    #: The head(s) who may decide the head step, fixed when it was sent.
    head_approver_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    #: "Tổng tiền mua thực tế" and "Nguồn mua", from the purchase step.
    actual_cost: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    supplier: Mapped[str | None] = mapped_column(String(200), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )


class ResourceRequestEvent(Base, UUIDPrimaryKeyMixin):
    """One line of a request's history: who did what, when. Never edited."""

    __tablename__ = RESOURCE_REQUEST_EVENTS

    request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{RESOURCE_REQUESTS}.id", ondelete=RESTRICT), nullable=False, index=True
    )
    kind: Mapped[RequestEventKind] = mapped_column(
        value_enum(RequestEventKind, name="resource_request_event_kind", length=20), nullable=False
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )


class ResourceCodeCounter(Base):
    """The last number handed out for one prefix (a department code)."""

    __tablename__ = RESOURCE_CODE_COUNTERS
    __table_args__ = (CheckConstraint("last_no >= 0", name="last_no_not_negative"),)

    prefix: Mapped[str] = mapped_column(String(12), primary_key=True)
    last_no: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class ResourceHandover(Base, UUIDPrimaryKeyMixin):
    """ "Lịch sử bàn giao": one line per change of holder. Append-only."""

    __tablename__ = RESOURCE_HANDOVERS
    __table_args__ = (Index("ix_resource_handovers_resource_id", "resource_id"),)

    resource_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{RESOURCES}.id", ondelete=RESTRICT), nullable=False
    )
    from_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )
    to_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )
    reason: Mapped[HandoverReason] = mapped_column(
        value_enum(HandoverReason, name="resource_handover_reason", length=12), nullable=False
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )
    #: Stamped in Python too, so the lines keep their order on a backend
    #: whose ``now()`` has only seconds (the offline suite).
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )


class ResourceStatusHistory(Base, UUIDPrimaryKeyMixin):
    """ "Lịch sử trạng thái": one line per status change, and one per review
    that changed nothing (``review_only``). Append-only."""

    __tablename__ = RESOURCE_STATUS_HISTORY
    __table_args__ = (Index("ix_resource_status_history_resource_id", "resource_id"),)

    resource_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{RESOURCES}.id", ondelete=RESTRICT), nullable=False
    )
    from_status_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{RESOURCE_OPTIONS}.id", ondelete=RESTRICT), nullable=True
    )
    to_status_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{RESOURCE_OPTIONS}.id", ondelete=RESTRICT), nullable=False
    )
    review_only: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )


class ResourceChange(Base, UUIDPrimaryKeyMixin):
    """ "Nhật ký thay đổi": one line per field changed (old → new). Append-only."""

    __tablename__ = RESOURCE_CHANGES
    __table_args__ = (Index("ix_resource_changes_resource_id", "resource_id"),)

    resource_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{RESOURCES}.id", ondelete=RESTRICT), nullable=False
    )
    field: Mapped[str] = mapped_column(String(40), nullable=False)
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )


# --- append-only, enforced by the database ----------------------------------------------

POSTGRES_IMMUTABLE_FUNCTION = """
CREATE OR REPLACE FUNCTION resource_history_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'resource history is append-only (%)', TG_TABLE_NAME
        USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql;
"""


def postgres_trigger_sql(table: str) -> str:
    return (
        f"CREATE TRIGGER trg_{table}_immutable BEFORE UPDATE OR DELETE ON {table} "
        f"FOR EACH ROW EXECUTE FUNCTION resource_history_immutable()"
    )


def sqlite_trigger_sql(table: str) -> tuple[str, str]:
    message = "resource history is append-only"
    return (
        f"CREATE TRIGGER trg_{table}_no_update BEFORE UPDATE ON {table} "
        f"BEGIN SELECT RAISE(ABORT, '{message}'); END",
        f"CREATE TRIGGER trg_{table}_no_delete BEFORE DELETE ON {table} "
        f"BEGIN SELECT RAISE(ABORT, '{message}'); END",
    )


for _model in (ResourceHandover, ResourceStatusHistory, ResourceChange):
    _table = cast(Table, _model.__table__)
    _postgres = [POSTGRES_IMMUTABLE_FUNCTION, postgres_trigger_sql(_table.name)]
    for _statement in _postgres:
        event.listen(
            _table,
            "after_create",
            DDL(_statement).execute_if(dialect="postgresql"),  # type: ignore[no-untyped-call]
        )
    for _statement in sqlite_trigger_sql(_table.name):
        event.listen(
            _table,
            "after_create",
            DDL(_statement).execute_if(dialect="sqlite"),  # type: ignore[no-untyped-call]
        )


__all__ = [
    "APPEND_ONLY_TABLES",
    "POSTGRES_IMMUTABLE_FUNCTION",
    "RESOURCES",
    "RESOURCE_CHANGES",
    "RESOURCE_CODE_COUNTERS",
    "RESOURCE_HANDOVERS",
    "RESOURCE_OPTIONS",
    "RESOURCE_STATUS_HISTORY",
    "Resource",
    "ResourceChange",
    "ResourceCodeCounter",
    "ResourceHandover",
    "ResourceOption",
    "ResourceStatusHistory",
    "postgres_trigger_sql",
    "sqlite_trigger_sql",
]
