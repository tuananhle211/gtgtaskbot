"""Content → Work projection: what maps to what, and what has been looked at.

Milestone M3, migration ``0034``. Two new tables and **nothing existing is
altered**. ``pr_content_items``, ``pr_approval_events``,
``pr_content_transition_events``, ``pr_publications`` and every work table come
through this revision byte for byte: the projector *reads* the content workflow
and writes only into the Work Ledger through the Work services, so the content
half of the system cannot be broken by anything M3 adds.

Why two tables and not none
----------------------------

The projector is a pure function of state everywhere it can be. Two things it
cannot derive:

* **which work type a content milestone is.** "A short-video script that was
  approved" is a fact; "that is 1 unit of ``SHORT_VIDEO_SCRIPT``" is a decision
  the department takes, and hard-coding it would put a business mapping in a
  deploy - see :class:`PrContentWorkRule`;
* **what still needs looking at.** Convergence needs a work list, and deriving
  one by scanning every content item every few seconds is the query this table
  exists to avoid - see :class:`PrContentWorkProjection`.

What is deliberately absent
----------------------------

**Every eligibility field.** No quota, no allocation, no ``quota_status``, no
points. M3 decides whether the content workflow produced trustworthy ``COUNTED``
work and stops there; M2 owns what that work is worth, and a column here that
cached any of it would be a second authority on the question M2 exists to be the
only authority on.
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
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from meobot.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, value_enum
from meobot.db.models.pr import RESTRICT, USERS_TABLE
from meobot.db.models.pr_work import WORK_TYPES
from meobot.domain.pr.content_work import (
    PrContentWorkKind,
    PrContentWorkOutcome,
    PrContentWorkProjectionStatus,
)
from meobot.domain.pr.models import PrContentType

CONTENT_WORK_RULES = "pr_content_work_rules"
CONTENT_WORK_PROJECTIONS = "pr_content_work_projections"
CONTENT_ITEMS = "pr_content_items"

#: The partial index predicates that make "one rule per case" a database fact.
#:
#: Two of them, because a nullable column cannot carry a unique constraint that
#: means what a reader expects: PostgreSQL treats every ``NULL`` as distinct, so
#: a plain unique on ``(contribution_kind, content_type)`` would happily accept
#: five competing default rules for one kind. Splitting the index in two says
#: the thing that is actually true - one rule per (kind, type), and one default
#: per kind - and says it in a way the database can hold.
TYPED_RULE = text("content_type IS NOT NULL")
DEFAULT_RULE = text("content_type IS NULL")


class PrContentWorkRule(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Which work type one kind of content milestone counts as.

    **The only administratively configurable part of M3.** Everything else - the
    milestones, who the contributor is, when a self-approved piece may count - is
    a domain rule in :mod:`meobot.domain.pr.content_work`, because those are the
    department's anti-gaming boundary rather than its taxonomy. A dropdown that
    let somebody choose *"content creation is complete when it is submitted"*
    would be a dropdown that turns the boundary off.

    What is configurable is the sentence *"a short-video script that gets
    approved is one ``SHORT_VIDEO_SCRIPT`` of work"*, and it is configurable
    because it is exactly the kind of thing that changes without a deploy: a new
    content type, a renamed work type, a department that decides press articles
    and YouTube scripts are measured differently.

    Exact beats default
    --------------------

    A rule with a ``content_type`` wins over one without, for the same kind. The
    default exists so a department can say *"anything approved is a script"* in
    one row and refine it later, and the specific one exists so that refining it
    does not mean enumerating everything else first. Resolution is one comparison
    in
    :class:`~meobot.application.pr_content_work_service.PrContentWorkRuleService`,
    never a query per candidate.

    **No mapping means no work - and, since ``0040``, no mapping is provisioned
    rather than left missing.** A content type nobody has mapped is bound by the
    projector to a work type it creates for exactly that type and kind, under a
    reserved code, and the deliverable that triggered it is counted in the same
    run. What is still never done is *guessing*: no fallback to whichever type
    sorts first, no match on a display name, and no binding at all for content
    that has no ``content_type`` to bind on. A rule an administrator has
    **deactivated** for a type is an explicit decision and is honoured - that
    type produces ``NO_MAPPING`` until somebody says otherwise.
    """

    __tablename__ = CONTENT_WORK_RULES
    __table_args__ = (
        # **One rule per (kind, content type).** What makes an ambiguous mapping
        # unrepresentable rather than resolved by whichever row came back first.
        Index(
            "uq_pr_content_work_rules_kind_type",
            "contribution_kind",
            "content_type",
            unique=True,
            postgresql_where=TYPED_RULE,
            sqlite_where=TYPED_RULE,
        ),
        # **One default per kind.** See ``TYPED_RULE`` on why this is a second
        # index rather than the same one.
        Index(
            "uq_pr_content_work_rules_kind_default",
            "contribution_kind",
            unique=True,
            postgresql_where=DEFAULT_RULE,
            sqlite_where=DEFAULT_RULE,
        ),
        # "Which rules apply to this kind" - the projector's only read of this
        # table, and it loads the whole small set once per run rather than once
        # per content item.
        Index("ix_pr_content_work_rules_kind_active", "contribution_kind", "is_active"),
    )

    contribution_kind: Mapped[PrContentWorkKind] = mapped_column(
        value_enum(PrContentWorkKind, name="pr_content_work_kind", length=30), nullable=False
    )
    #: Which classification this rule is for. ``NULL`` is **the default for the
    #: kind**, not "unclassified content": a piece whose ``content_type`` is
    #: itself ``NULL`` also falls to the default, and the two meanings coincide
    #: usefully rather than by accident - neither has a more specific answer.
    content_type: Mapped[PrContentType | None] = mapped_column(
        value_enum(PrContentType, name="pr_content_type", length=30), nullable=True
    )
    work_type_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{WORK_TYPES}.id", ondelete=RESTRICT), nullable=False, index=True
    )
    #: Deactivated rules stop mapping and keep their history. Never deleted while
    #: work exists under them - the work item records the type it was filed as,
    #: so turning a rule off changes what happens next and rewrites nothing.
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    #: Why this mapping exists. Prose for whoever reads it in a year.
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Who decided this mapping. ``NULL`` since ``0040`` means **the system
    #: provisioned it** from the content workflow - the projector met a content
    #: type nobody had mapped yet and bound it rather than losing the first
    #: accepted deliverable - and that is the whole of the provenance marker: a
    #: rule a person wrote names them, a rule the projector wrote names nobody.
    #: Never rewritten by a later edit, so the row keeps saying how it began.
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )

    @property
    def auto_provisioned(self) -> bool:
        """Whether the projector wrote this rule rather than a person."""
        return self.created_by_user_id is None


class PrContentWorkProjection(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One content item's standing with the projector. Queue **and** diagnostic.

    **One row per content item, for ever** - not one per request. Projection is
    convergent: it reads the current source facts and makes the Work Ledger
    match, so two outstanding requests for one piece are the same request, and
    collapsing them is the whole reason the unique key is ``content_id`` rather
    than a serial.

    That also makes the row worth keeping after it settles. *"When was this last
    projected, what did it conclude, how many times has it failed"* are the
    questions a reconciliation report has to answer, and a queue that deleted
    its rows would answer none of them - the operator would be left reading logs
    to find out whether silence meant success.

    Why a queue at all
    -------------------

    The alternative was a sweeper that scans content by ``updated_at``, and it
    fails in the direction that matters: a worker down for an hour misses
    everything in the window it was down for, and no row anywhere records that
    it did. A request row written **inside the content transaction** cannot be
    missed - if the transition committed, so did the request - and it is the
    same handoff shape ``pr_ai_review_runs`` already uses, for the same reason.

    The cost is one small insert on a content transition. It is a single row
    with no foreign key beyond the content item, and it is an upsert, so a piece
    that moves five times before the sweeper wakes writes one row and touches it
    four times.
    """

    __tablename__ = CONTENT_WORK_PROJECTIONS
    __table_args__ = (
        CheckConstraint("attempts >= 0", name="attempts_not_negative"),
        # **One standing per content item.** What makes "request projection"
        # idempotent, and what a repeated content transition collapses onto.
        Index("uq_pr_content_work_projections_content", "content_id", unique=True),
        # "What is waiting, oldest first" - the sweeper's only query, and the
        # reason it can claim a bounded batch without a scan.
        Index("ix_pr_content_work_projections_status_requested", "status", "requested_at"),
    )

    content_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{CONTENT_ITEMS}.id", ondelete=RESTRICT), nullable=False
    )
    status: Mapped[PrContentWorkProjectionStatus] = mapped_column(
        value_enum(
            PrContentWorkProjectionStatus, name="pr_content_work_projection_status", length=20
        ),
        nullable=False,
        default=PrContentWorkProjectionStatus.PENDING,
        server_default=PrContentWorkProjectionStatus.PENDING.value,
        index=True,
    )
    #: When something last happened to the content that could change its work.
    #: Moved forward by every request, so a piece that changes while a worker
    #: holds it is re-queued rather than settled against stale facts.
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    #: When a worker took it. The staleness clock: a claim older than the
    #: configured window is assumed lost and returned to ``PENDING``, exactly as
    #: ``pr_ai_review_runs`` recovers a worker that died mid-review.
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: When the projector last reached an answer, whatever the answer was.
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: How many runs this content has cost. Not a retry budget - the projector
    #: is convergent and safe to run for ever - but the number that says *"this
    #: one is not getting better"* to somebody reading the report.
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    #: What the last run concluded, in the diagnostic vocabulary. **Not a quota
    #: status**: ``UNRESOLVED_CONTRIBUTOR`` says the projector cannot name whose
    #: work this was, which has nothing to do with whether a quota exists - see
    #: :class:`~meobot.domain.pr.content_work.PrContentWorkOutcome`.
    last_outcome: Mapped[PrContentWorkOutcome | None] = mapped_column(
        value_enum(PrContentWorkOutcome, name="pr_content_work_outcome", length=30), nullable=True
    )
    #: A short stable code when a run failed unexpectedly. Never an exception
    #: message: an error string is an implementation detail, and storing one as
    #: operational state puts a stack trace on an admin screen.
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)


__all__: list[str] = [
    "CONTENT_WORK_PROJECTIONS",
    "CONTENT_WORK_RULES",
    "PrContentWorkProjection",
    "PrContentWorkRule",
]
