"""A Biên kịch / Dựng worker edits their task codes while working.

Revision ID: 0089
Revises: 0088
Create Date: 2026-11-03

Additive. Until now a Biên kịch / Dựng step's own task codes were picked only
in the hand-in dialog. Once the worker has taken the step ("Nhận việc") they
may now keep their list up to date while they work ("Sửa mã task"): it is
saved on the node as ``working_task_items`` and fills the hand-in dialog. It
is a draft - the hand-in still compares what is handed in with the
suggestion (``task_items``), and a different list still waits for the
step's Trưởng phòng.

Downgrade
---------

Drops the column (the drafts are lost; the hand-ins are not).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0089"
down_revision: str | None = "0088"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ORDER_NODES = "order_nodes"
_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column(ORDER_NODES, sa.Column("working_task_items", _JSON, nullable=True))


def downgrade() -> None:
    op.drop_column(ORDER_NODES, "working_task_items")
