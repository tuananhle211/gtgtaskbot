"""Every stage change a content item ever made. Append-only.

Step 1F.2.3b. Until now the history of a piece's movement lived in the audit
trail - ``pr.content.stage_changed`` rows with the stages in a JSON payload -
and that was enough while the only consumer was a person reading a log. Undo is
a different kind of consumer: it has to answer *"what was the last reversible
thing that happened, and is it still the thing that put this content where it
is"* as a **business** question, in SQL, under a lock. Deriving that from
free-form audit text would mean parsing a payload nothing constrains, and the
first time somebody changed a key the undo button would start reversing the
wrong action.

So the transitions get a table. The audit trail is unchanged and still records
every move; this is the structured half, and the two are written in the same
transaction by the same method.

What one row is
---------------

One edge, taken once: ``from_stage -> to_stage``, by whom, driven by what, and
pointing at whatever made it legal - the approval event, the production
submission, the draft. Written by
:meth:`~meobot.application.pr_workflow_service.PrContentWorkflowService.apply`,
which is already the only writer of ``workflow_stage``, so a stage change
without a row here is not a state this module can produce.

The reversal pair
-----------------

An undo does not edit anything. It appends a second row - the backward edge,
with ``trigger = UNDO`` - and links the two:

* the new row's ``reverses_event_id`` points at the row it takes back;
* the original row's ``reversed_by_event_id`` points forward at the new one.

``reversed_by_event_id`` is the **one** mutable column in this table, and it is
write-once: null until the transition is reversed, set exactly once, never
cleared. That is a deliberate exception to append-only and it buys the query the
whole feature turns on - *"is this transition still in force"* - as an index
lookup rather than a correlated search for a reversal that may not exist. The
check constraint below is what keeps the pair honest: a row may not reverse
itself, and only an ``UNDO`` row may reverse anything.

Why an approval link rather than a flag on the approval
--------------------------------------------------------

``pr_approval_events`` is append-only and has no ``updated_at``; marking an
approval "cancelled" in place would be the edit that table exists to forbid. So
the reversal lives here, and "is this approval still effective" becomes "is the
transition that recorded it un-reversed" - one join, no mutation, and the
approval row still says exactly what it said on the day somebody signed it.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from meobot.db.base import Base, UUIDPrimaryKeyMixin, value_enum
from meobot.db.models.pr import RESTRICT, USERS_TABLE
from meobot.domain.pr.models import PrWorkflowStage
from meobot.domain.pr.workflow import PrTransitionTrigger


class PrContentTransitionEvent(Base, UUIDPrimaryKeyMixin):
    """One content-stage change, and its reversal link if it has one."""

    __tablename__ = "pr_content_transition_events"
    __table_args__ = (
        CheckConstraint(
            "reverses_event_id IS NULL OR reverses_event_id <> id", name="no_self_reversal"
        ),
        # "The history of this item, in order" - what the detail page renders and
        # what undo reads to find the latest effective transition. Both need the
        # same index, so there is one.
        Index("ix_pr_content_transition_events_content_created", "content_id", "created_at"),
        Index("ix_pr_content_transition_events_approval", "approval_event_id"),
    )

    content_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pr_content_items.id", ondelete=RESTRICT), nullable=False
    )
    #: The draft that was current when the move happened. Nullable: a brand-new
    #: item can move from ``IDEA`` before anybody writes anything, and Step
    #: 1F.2.3b's revision-undo rule reads this to notice that a *newer* draft has
    #: been written since - which is what makes "do not throw away new work"
    #: checkable rather than a matter of timing.
    content_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pr_content_versions.id", ondelete=RESTRICT), nullable=True
    )
    from_stage: Mapped[PrWorkflowStage] = mapped_column(
        value_enum(PrWorkflowStage, name="pr_workflow_stage", length=30), nullable=False
    )
    to_stage: Mapped[PrWorkflowStage] = mapped_column(
        value_enum(PrWorkflowStage, name="pr_workflow_stage", length=30), nullable=False
    )
    #: What was entitled to drive this edge - ``MANUAL``, ``AI_REVIEW``,
    #: ``HUMAN_APPROVAL`` or ``UNDO``. The same vocabulary the matrix validates
    #: against, so "which kind of thing was this" needs no second enum.
    trigger: Mapped[PrTransitionTrigger] = mapped_column(
        value_enum(PrTransitionTrigger, name="pr_transition_trigger", length=20), nullable=False
    )
    #: Who moved it. Nullable for the same reason ``audit_logs.actor_user_id``
    #: is: a worker settling an AI review has no ``users`` row behind it.
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True, index=True
    )
    #: The decision that caused this move, for a ``HUMAN_APPROVAL`` edge. This is
    #: the link that makes an approval reversible without touching its row.
    approval_event_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pr_approval_events.id", ondelete=RESTRICT), nullable=True
    )
    #: The cut that caused this move, for ``PRODUCTION -> INTERNAL_REVIEW``.
    production_submission_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pr_production_submissions.id", ondelete=RESTRICT), nullable=True
    )
    #: Set on an ``UNDO`` row: the transition this one takes back.
    reverses_event_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pr_content_transition_events.id", ondelete=RESTRICT), nullable=True
    )
    #: Set on the row that was taken back. Write-once - see the module docstring.
    reversed_by_event_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pr_content_transition_events.id", ondelete=RESTRICT), nullable=True
    )
    #: Why, in the actor's words, when they gave one. Prose; nothing parses it.
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: No ``updated_at``: the only write after insert is the reversal link, and
    #: that is a dated fact recorded on the *other* row.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    @property
    def is_reversed(self) -> bool:
        return self.reversed_by_event_id is not None


__all__: list[str] = ["PrContentTransitionEvent"]
