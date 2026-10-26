"""``user_point_coefficients`` (0076): a member's points coefficient by month.

"Hệ số điểm" (2026-10-26): every point a person earns in a month is
multiplied by their coefficient for that month (1 when none is set). A row
says *from this month on, this person's coefficient is X*: the coefficient of
a month is the row with the latest ``effective_month`` not after it, the
latest written when one month has several. Rows are never changed nor deleted
- a new row supersedes - so last month's report stays explicable.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, Numeric, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from meobot.core.time import utcnow
from meobot.db.base import Base, UUIDPrimaryKeyMixin
from meobot.db.models.pr import RESTRICT, USERS_TABLE

USER_POINT_COEFFICIENTS = "user_point_coefficients"


class UserPointCoefficient(Base, UUIDPrimaryKeyMixin):
    __tablename__ = USER_POINT_COEFFICIENTS
    __table_args__ = (
        CheckConstraint("coefficient > 0 AND coefficient <= 10", name="coefficient_range"),
        Index("ix_user_point_coefficients_user_month", "user_id", "effective_month"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=False
    )
    #: The first day of the month it applies from.
    effective_month: Mapped[date] = mapped_column(Date, nullable=False)
    coefficient: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )


__all__ = ["USER_POINT_COEFFICIENTS", "UserPointCoefficient"]
