"""Declarative base, shared column types and mixins.

Type choices are deliberate:

* ``Uuid`` (SQLAlchemy generic) renders as native ``UUID`` on PostgreSQL.
* ``JSON().with_variant(JSONB, "postgresql")`` gives us JSONB in production
  while keeping models loadable against other backends in tests.
* Enums are stored as ``VARCHAR`` + CHECK constraint (``native_enum=False``)
  rather than PostgreSQL ENUM types: adding a workflow state must not require
  an ``ALTER TYPE`` migration, and the values stay greppable.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import JSON, DateTime, MetaData, Uuid, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

#: Deterministic constraint names keep Alembic autogenerate diffs clean.
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

#: JSON column type: JSONB on PostgreSQL, plain JSON elsewhere.
JSONColumn = JSON().with_variant(JSONB(), "postgresql")


def value_enum(python_enum: type[enum.Enum], *, name: str, length: int) -> SAEnum:
    """A ``VARCHAR`` enum column that stores member **values**.

    SQLAlchemy stores an enum member's ``name`` by default. For an enum whose
    name and value differ - ``UserStatus.ACTIVE`` is ``"active"``,
    ``AccessAction.ANSWER_ONCE`` is ``"ao"`` - that puts a different string in
    the column from the one the migration declares, the API returns, and the
    tests assert. ``values_callable`` pins it to the value, so what the code
    says and what is on disk are the same string.

    Use this for every new enum column. The pre-existing ones are left as they
    are: changing them would rewrite stored data for no behavioural gain.
    """
    return SAEnum(
        python_enum,
        name=name,
        native_enum=False,
        length=length,
        validate_strings=True,
        values_callable=lambda members: [str(member.value) for member in members],
    )


class Base(DeclarativeBase):
    """Base class for all MeoBot ORM models."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    type_annotation_map: ClassVar[dict[Any, Any]] = {
        dict[str, Any]: JSONColumn,
        uuid.UUID: Uuid(as_uuid=True),
        datetime: DateTime(timezone=True),
    }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        pk = getattr(self, "id", None)
        return f"<{type(self).__name__} id={pk}>"


class UUIDPrimaryKeyMixin:
    """UUIDv4 primary key generated client-side (no DB extension required)."""

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )


class TimestampMixin:
    """``created_at`` / ``updated_at`` maintained by the database in UTC."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
