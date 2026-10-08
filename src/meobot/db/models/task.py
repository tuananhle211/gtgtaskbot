"""One task per piece of work, in either unit.

Added by ``0043``. A PR content item (``pr_content_items``) and an Ads order
(``orders``) each get exactly one ``tasks`` row, and the row is a
**projection**: it is written by the flush hook in
:mod:`meobot.application.tasks.sync` whenever its source row is written, and
nothing else writes it. The PR workflow and the order engine keep every rule
and every column they had; this table is what lets one screen, one URL
(``/tasks/{code}``) and one list speak of both.

Both source keys cascade: a task is a picture of its source and has no history
of its own, so a source row that is deleted (PR's permanent delete is a plain
``DELETE``) takes its picture with it.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from meobot.core.time import utcnow
from meobot.db.base import Base, UUIDPrimaryKeyMixin
from meobot.db.models.order import ORDERS, Order
from meobot.db.models.org_unit import ORG_UNITS
from meobot.db.models.pr import RESTRICT, USERS_TABLE, PrContentItem

TASKS = "tasks"
CASCADE = "CASCADE"

#: The two sources, spelled as stored.
SOURCE_PR_CONTENT = "PR_CONTENT"
SOURCE_ORDER = "ORDER"

#: Exactly one source key, and the one the type names.
ONE_SOURCE = (
    "(source_type = 'PR_CONTENT' AND pr_content_id IS NOT NULL AND order_id IS NULL) OR "
    "(source_type = 'ORDER' AND order_id IS NOT NULL AND pr_content_id IS NULL)"
)
PHASE_VALUES = "phase IN ('ORDER', 'REVIEW', 'PRODUCTION', 'FINAL_REVIEW', 'DONE', 'CANCELLED')"


class Task(Base, UUIDPrimaryKeyMixin):
    """The unit-neutral picture of one PR content item or one Ads order."""

    __tablename__ = TASKS
    __table_args__ = (
        CheckConstraint(ONE_SOURCE, name="one_source"),
        CheckConstraint(PHASE_VALUES, name="phase_valid"),
        CheckConstraint("length(trim(code)) > 0", name="code_not_empty"),
    )

    unit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{ORG_UNITS}.id", ondelete=RESTRICT), nullable=False, index=True
    )
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    pr_content_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pr_content_items.id", ondelete=CASCADE), nullable=True, unique=True
    )
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{ORDERS}.id", ondelete=CASCADE), nullable=True, unique=True
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    #: PR content type or Ads video type; empty for an unclassified PR item.
    kind: Mapped[str] = mapped_column(
        String(30), nullable=False, default="", server_default=text("''")
    )
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=False, index=True
    )
    is_priority: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    #: The source's own stage (``PrWorkflowStage`` / ``OrderStage`` value).
    stage: Mapped[str] = mapped_column(String(30), nullable=False)
    #: :class:`~meobot.domain.board.models.Phase` value.
    phase: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    stage_since: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        server_default=func.now(),
        nullable=False,
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )

    # Write-side links only: they make the unit of work insert the source
    # before its task inside one flush. Nothing reads through them.
    pr_content: Mapped[PrContentItem | None] = relationship(
        PrContentItem, foreign_keys=[pr_content_id], lazy="raise", passive_deletes=True
    )
    order: Mapped[Order | None] = relationship(
        Order, foreign_keys=[order_id], lazy="raise", passive_deletes=True
    )


__all__ = [
    "SOURCE_ORDER",
    "SOURCE_PR_CONTENT",
    "TASKS",
    "Task",
]
