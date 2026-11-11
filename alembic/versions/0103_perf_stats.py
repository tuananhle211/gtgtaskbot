"""Theo dõi tốc độ: ``perf_stats_hourly``.

Revision ID: 0103
Revises: 0102
Create Date: 2026-11-11

Plan ``docs/ops/SCALING_PLAN.md`` D: per hour, how long API requests took
(by route template) and how long background tasks waited in their queue (by
task name) - counts, sums, the longest, a histogram for the p95. No person,
no path, no content. Rows are added to by every process; older than 30 days
are deleted by the writers.

Downgrade
---------

Drops the table (the figures are only for the "Hiệu năng" screen).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0103"
down_revision: str | None = "0102"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "perf_stats_hourly",
        sa.Column("hour", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("method", sa.String(length=10), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("total_ms", sa.BigInteger(), nullable=False),
        sa.Column("max_ms", sa.Integer(), nullable=False),
        sa.Column("slow", sa.Integer(), nullable=False),
        sa.Column("failed", sa.Integer(), nullable=False),
        sa.Column("dropped", sa.Integer(), nullable=False),
        *[sa.Column(f"b{index}", sa.Integer(), nullable=False) for index in range(9)],
        sa.PrimaryKeyConstraint("hour", "kind", "method", "name", name="pk_perf_stats_hourly"),
    )


def downgrade() -> None:
    op.drop_table("perf_stats_hourly")
