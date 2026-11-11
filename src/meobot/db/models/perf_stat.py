"""``perf_stats_hourly`` - response and queue times per hour (0103).

See :mod:`meobot.domain.ops.perf_stats`. Written by adding to a row (an
upsert), so every API process / worker child may write its own share.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from meobot.db.base import Base

#: Column names of the histogram, in bucket order.
BUCKET_COLUMNS = tuple(f"b{index}" for index in range(9))


class PerfStatHourly(Base):
    __tablename__ = "perf_stats_hourly"

    hour: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    kind: Mapped[str] = mapped_column(String(10), primary_key=True)
    method: Mapped[str] = mapped_column(String(10), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_ms: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    max_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    slow: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dropped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    b0: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    b1: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    b2: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    b3: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    b4: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    b5: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    b6: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    b7: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    b8: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


__all__ = ["BUCKET_COLUMNS", "PerfStatHourly"]
