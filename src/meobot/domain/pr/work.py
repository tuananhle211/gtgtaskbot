"""The Work Ledger's vocabulary: what work is, how it moves, and when it counts.

Milestone M1. This module is the whole of the Work module's *rules* - the
statuses, the legal edges between them, what "overdue" means, and the format of
a source key. It knows nothing about SQL, HTTP or Telegram, which is what lets
:mod:`meobot.application.pr_work_service` be argued about in one place and
tested without a database.

The one idea this module exists to protect
-------------------------------------------

**CREATED != ACCEPTED != COMPLETED != APPROVED != COUNTED.**

Five different facts about one piece of work, and collapsing any two of them is
how a KPI becomes something an employee can write for themselves. So they are
five different columns in two different tables, and the only path from the
fourth to the fifth runs through
:meth:`~meobot.application.pr_work_service.PrWorkService.approve`, which refuses
an actor who is one of the contributors.

Why a work item and a contribution are different rows
------------------------------------------------------

A shoot with three people is **one** job and **three** people's workload. The
department did one shoot; each of the three did a day's work. Reporting has to
be able to say both, so:

* :class:`PrWorkStatus` lives on the item and describes *the job*;
* :class:`PrWorkCountStatus` lives on the contribution and describes *one
  person's credit*.

A single status could not answer both questions, and a count on the item would
have to be divided by three or multiplied by three - both of which are wrong.

What is deliberately absent
----------------------------

**Every score.** No base score, no multiplier, no quality grade, no quota, no
cap, no ``score_status``, no ``OVER_QUOTA``. M1 answers *"is this valid completed
work"* and stops. M2 decides eligibility against a quota and M6 computes points;
neither of them needs to change what :attr:`PrWorkCountStatus.COUNTED` means,
which is the property M1 is built to hand over.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType

from meobot.domain.pr.errors import PrValidationError


class PrWorkCategory(StrEnum):
    """The coarse grouping a work type belongs to.

    An **enum** rather than a table, unlike the sub-type beside it, and the
    split is the point of the hybrid taxonomy: this is a small closed set that
    *code* groups and reports by, while the sub-type is a long open list the
    business owns. Stored as ``VARCHAR`` + CHECK like every other MeoBot enum,
    so adding a category is a code change with no migration.

    ``OTHER`` is a **fallback and not a category**. Work that lands there is
    work whose type nobody has modelled yet, and a report showing a large
    ``OTHER`` bar is telling somebody to add a work type - not describing a kind
    of work.
    """

    CONTENT = "CONTENT"
    PRODUCTION = "PRODUCTION"
    DISTRIBUTION = "DISTRIBUTION"
    COMMUNITY = "COMMUNITY"
    PR_EVENT = "PR_EVENT"
    OPERATIONS = "OPERATIONS"
    RESEARCH = "RESEARCH"
    OTHER = "OTHER"


class PrWorkUnit(StrEnum):
    """What one unit of ``quantity`` counts.

    Closed on purpose. The alternative - free text - is how "comment",
    "comments", "cmt" and "bình luận" end up in one column and a report has to
    guess. A unit that is genuinely missing is a code change; a unit somebody
    typed is a reporting bug nobody notices for a month.

    The unit is chosen by the **work type**, not by the person filing the work.
    That is what stops one real job being split into whichever shape pays best -
    see :data:`~meobot.domain.pr.work` module docstring and
    :meth:`~meobot.application.pr_work_service.PrWorkService.create_work`.
    """

    #: The default. One deliverable, whatever it is.
    ITEM = "ITEM"
    VIDEO = "VIDEO"
    POST = "POST"
    ARTICLE = "ARTICLE"
    COMMENT = "COMMENT"
    MESSAGE = "MESSAGE"
    #: One social account being grown or looked after. M2.5, for
    #: ``ACCOUNT_CARE``: the work is measured in accounts, and recording it in
    #: ``ITEM`` would make "ten accounts" and "ten deliverables" the same
    #: number in a report that means to distinguish them.
    ACCOUNT = "ACCOUNT"
    #: A meeting, a livestream, a shoot - something with a start and an end.
    SESSION = "SESSION"
    HOUR = "HOUR"
    #: A shooting day. ``0.5`` is a half-day shoot, which is why quantity is a
    #: decimal rather than an integer.
    DAY = "DAY"
    #: One customer found or converted. Added by the period-container patch for
    #: *Tìm khách hàng*, whose results were being filed as ``ITEM`` and read
    #: back as "sản phẩm".
    CUSTOMER = "CUSTOMER"
    #: One script written. Distinct from ``ITEM`` so a scripts stream reads
    #: "23 kịch bản" rather than "23 sản phẩm".
    SCRIPT = "SCRIPT"
    #: One order closed.
    ORDER = "ORDER"


class PrWorkSourceType(StrEnum):
    """Where a work item came from.

    Five members, and **M1 writes exactly one of them**. The other four are
    declared now because :attr:`source_key`'s unique index has to exist before
    anything can be counted - an idempotency guarantee added after the first
    projector runs is an idempotency guarantee that has already failed once.
    """

    #: A person filed it. The only source M1's APIs produce.
    MANUAL = "MANUAL"
    #: Derived from the content workflow reaching a milestone. **M3.**
    CONTENT = "CONTENT"
    #: Derived from a ``pr_tasks`` row. **M4**, and only if the bridge is built.
    TASK = "TASK"
    #: Generated from a recurring template. **M4.**
    RECURRING = "RECURRING"
    #: Generated by MeoBot itself for operational work. Reserved.
    SYSTEM = "SYSTEM"


class PrWorkStatus(StrEnum):
    """Where one job has got to. About the **job**, never about a person.

    Seven, and each earns its place. In particular there is no ``BLOCKED`` and
    no ``DONE``: blocked work is accepted work that has not started, and there
    is no state after :attr:`APPROVED` for M1 to distinguish.
    """

    #: An employee suggested this. **Not yet work**: it is in nobody's workload,
    #: its contributions are ``PENDING``, and no report counts it. The state
    #: that makes "an employee cannot create their own KPI" structural.
    PROPOSED = "PROPOSED"
    #: A manager assigned it, or accepted somebody's proposal. Real work now.
    ACCEPTED = "ACCEPTED"
    IN_PROGRESS = "IN_PROGRESS"
    #: A contributor says it is finished. **Still not counted** - this is the
    #: state the anti-gaming rule exists around, and the Vietnamese label says
    #: so: *"Chờ xác nhận"*.
    COMPLETED = "COMPLETED"
    #: Somebody who is **not** a contributor confirmed it. The only state in
    #: which a contribution may be ``COUNTED``. Terminal in M1 - see
    #: :data:`TERMINAL_WORK_STATUSES`.
    APPROVED = "APPROVED"
    #: A proposal that was not taken on. Terminal.
    REJECTED = "REJECTED"
    #: Abandoned. Terminal, and never a deletion: the row and its history stay.
    CANCELLED = "CANCELLED"


class PrWorkContributionRole(StrEnum):
    """In what capacity somebody worked on a job.

    Three, and deliberately **no ``REVIEWER``**. Whether reviewing is itself
    countable work is an M3 question about the content workflow; adding the
    value now would put a role in the vocabulary that no M1 path can justify.
    """

    #: The person answerable for the job. Exactly one is expected, not enforced.
    PRIMARY = "PRIMARY"
    CONTRIBUTOR = "CONTRIBUTOR"
    #: Helped, without owning any part of it.
    SUPPORT = "SUPPORT"


class PrWorkAssignmentMode(StrEnum):
    """What "assign this to three people" is being asked for. **M4A.**

    Two genuinely different business facts that one multi-person assignment
    form would otherwise collapse:

    * :attr:`SHARED_WORK` - *one* job several people worked on. A half-day
      shoot with a producer, a camera operator and an assistant is one shoot,
      and the department did one shoot. One :class:`PrWorkItem`, three
      :class:`PrWorkContribution` rows;
    * :attr:`SEPARATE_PER_ASSIGNEE` - *the same instruction* handed to several
      people, each answerable for their own. "100 comments today" given to
      three people is three jobs and three separate obligations. Three work
      items, one contribution each.

    Collapsing the second into the first is the dangerous direction and the
    reason this enum exists: three people's independent responsibilities
    recorded as one shared item means one person completing it completes it for
    all three, one validation counts all three, and nobody can be late on their
    own. The reverse mistake merely over-counts jobs on a department report.

    **Not a column.** This is an instruction about how to *create* work, not a
    fact about the work that results: a shared item is fully described by its
    three contributions and a separate one by its single contribution, and a
    stored mode would be a field nothing reads and nothing keeps true. It
    therefore needs no migration - see ``docs/pr/MANUAL_RECURRING_WORK_M4.md``.

    M4B reuses it as a **template** field, where it genuinely is stored: there
    the mode has to survive until the next occurrence is generated, so it is a
    fact about the template even though it is not a fact about the work.
    """

    #: One job, several contributors.
    SHARED_WORK = "SHARED_WORK"
    #: One job each. **The safer default** for an operational instruction, and
    #: what the API requires be stated explicitly whenever more than one person
    #: is named - see
    #: :meth:`~meobot.application.pr_work_service.PrWorkService.assign_work_batch`.
    SEPARATE_PER_ASSIGNEE = "SEPARATE_PER_ASSIGNEE"


class PrWorkCountStatus(StrEnum):
    """Whether **this person's** share of a job is valid completed work.

    Three values, which is the smallest vocabulary that says the true thing.
    Deliberately **not** a boolean: "not counted yet" and "deliberately not
    counted" need different sentences on a screen and different treatment if
    the work is later revisited.

    There is no ``SCORED`` and no ``OVER_QUOTA``. Whether counted work earns
    points is M2's question and M6's arithmetic, and neither changes what this
    enum means.
    """

    #: The default. The work has not been independently validated.
    PENDING = "PENDING"
    #: Validated by somebody who did not do it. This is the number a KPI reads.
    COUNTED = "COUNTED"
    #: Will never count. Written when a job is cancelled, and reserved for the
    #: manual exclusion M2 adds.
    EXCLUDED = "EXCLUDED"


class PrWorkEventType(StrEnum):
    """What one row of ``pr_work_history`` records.

    The **user-facing** history, which is a different thing from the audit
    trail: this is what a person reads on a work item's timeline, so it is
    phrased as things that happened to the work rather than as security events.
    ``audit_logs`` still receives every one of these with its structured
    before/after payload - see
    :mod:`meobot.application.pr_work_service` on why both exist and why they do
    not carry the same content.
    """

    CREATED = "CREATED"
    PROPOSED = "PROPOSED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    ASSIGNED = "ASSIGNED"
    CONTRIBUTOR_ADDED = "CONTRIBUTOR_ADDED"
    CONTRIBUTOR_REMOVED = "CONTRIBUTOR_REMOVED"
    STARTED = "STARTED"
    COMPLETED = "COMPLETED"
    #: A validator sent finished work back for more.
    REOPENED = "REOPENED"
    APPROVED = "APPROVED"
    #: One contribution became valid completed work. Written per person, so a
    #: three-person shoot leaves three of these beside one ``APPROVED``.
    COUNTED = "COUNTED"
    EXCLUDED = "EXCLUDED"
    DEADLINE_CHANGED = "DEADLINE_CHANGED"
    PRIORITY_CHANGED = "PRIORITY_CHANGED"
    EVIDENCE_ADDED = "EVIDENCE_ADDED"
    EVIDENCE_REMOVED = "EVIDENCE_REMOVED"
    CANCELLED = "CANCELLED"
    #: A result was declared on a period container. Per result, so a month's
    #: timeline reads "+3, +5, +2" rather than one line saying "changed".
    RESULT_REPORTED = "RESULT_REPORTED"
    #: A result was independently validated and now adds to the actual.
    RESULT_COUNTED = "RESULT_COUNTED"
    #: The source took the fact underneath a result away - an approval undone,
    #: a mapping moved - and the projector took the result out with it.
    RESULT_EXCLUDED = "RESULT_EXCLUDED"
    #: The reporter withdrew their own pending result.
    RESULT_WITHDRAWN = "RESULT_WITHDRAWN"
    #: A validator reviewed a result and refused to count it, with a reason.
    #: ``0041``. A decision no projection reverses - see ``RESULT_RECONSIDERED``.
    RESULT_REJECTED = "RESULT_REJECTED"
    #: A validator released a rejection back to ``PENDING`` for review again.
    RESULT_RECONSIDERED = "RESULT_RECONSIDERED"
    #: An administrator took a result out of the accounting. Not a decision
    #: about the source: the next projection re-evaluates it.
    RESULT_ADMIN_REMOVED = "RESULT_ADMIN_REMOVED"


# ---------------------------------------------------------------------------
# The lifecycle
# ---------------------------------------------------------------------------

#: Every legal edge. Anything not listed is refused by
#: :func:`assert_work_transition`, and there is no path around it: the service
#: has no method that writes ``status`` without going through this table.
#:
#: Three edges are worth reading twice:
#:
#: * ``ACCEPTED -> COMPLETED`` skips ``IN_PROGRESS``. Small work does not need a
#:   start, and forcing one would train people to press two buttons at once;
#: * ``COMPLETED -> IN_PROGRESS`` is a validator sending work back. Without it a
#:   validator's only alternatives are approving work that is not done or
#:   cancelling somebody's afternoon;
#: * ``APPROVED`` has **no outgoing edges at all**. See
#:   :data:`TERMINAL_WORK_STATUSES`.
WORK_TRANSITIONS: Mapping[PrWorkStatus, frozenset[PrWorkStatus]] = MappingProxyType(
    {
        PrWorkStatus.PROPOSED: frozenset(
            {PrWorkStatus.ACCEPTED, PrWorkStatus.REJECTED, PrWorkStatus.CANCELLED}
        ),
        PrWorkStatus.ACCEPTED: frozenset(
            {PrWorkStatus.IN_PROGRESS, PrWorkStatus.COMPLETED, PrWorkStatus.CANCELLED}
        ),
        PrWorkStatus.IN_PROGRESS: frozenset({PrWorkStatus.COMPLETED, PrWorkStatus.CANCELLED}),
        PrWorkStatus.COMPLETED: frozenset(
            {PrWorkStatus.APPROVED, PrWorkStatus.IN_PROGRESS, PrWorkStatus.CANCELLED}
        ),
        # ``COMPLETED`` is here for **source-derived work only**, and
        # ``assert_work_transition`` refuses it for everything else - see
        # :data:`SOURCE_ONLY_TRANSITIONS`.
        PrWorkStatus.APPROVED: frozenset({PrWorkStatus.COMPLETED}),
        PrWorkStatus.REJECTED: frozenset(),
        PrWorkStatus.CANCELLED: frozenset(),
    }
)

#: Edges **only a source projector may take**.
#:
#: One member, added by M3, and the guard around it is the point. M1 made
#: ``APPROVED`` terminal and said why: taking back a ``counted_at`` is a
#: correction against a reporting period that may since have been closed, and
#: *"correction semantics belong to the milestone that has a quota engine to
#: stay consistent with"*. M2 shipped that engine, and M3 is the milestone that
#: needs the edge - a content approval that is undone must not leave work
#: counted for a deliverable the department no longer accepts.
#:
#: **Manual work is unchanged.** :func:`assert_work_transition` refuses this
#: edge unless the caller says the item is source-derived, and the only method
#: that says so refuses anything but ``source_type = CONTENT``. So "a person
#: cannot un-approve work somebody validated" is still true of every row a
#: person filed, and the new edge exists exactly where the source, not a person,
#: is the authority on whether the work happened.
SOURCE_ONLY_TRANSITIONS: frozenset[tuple[PrWorkStatus, PrWorkStatus]] = frozenset(
    {(PrWorkStatus.APPROVED, PrWorkStatus.COMPLETED)}
)

#: Statuses nothing moves out of **for work a person filed**.
#:
#: ``APPROVED`` is in here, and that is an **M1 product decision** rather than
#: an oversight: approved work has already written ``counted_at`` onto its
#: contributions, and taking that back is a correction against a reporting
#: period that may since have been closed or locked. For manual work that is
#: still the answer - validation is final, and a mistake is recorded as a new
#: piece of work rather than by rewriting history.
#:
#: M3 added the one exception, and only for work whose *source* can withdraw the
#: fact underneath it: see :data:`SOURCE_ONLY_TRANSITIONS`.
TERMINAL_WORK_STATUSES: frozenset[PrWorkStatus] = frozenset(
    {PrWorkStatus.APPROVED, PrWorkStatus.REJECTED, PrWorkStatus.CANCELLED}
)

#: Work that is still somebody's problem. What the operational views list, and
#: what a period filter must never remove from them - see
#: :func:`is_overdue`.
OPEN_WORK_STATUSES: frozenset[PrWorkStatus] = frozenset(
    {
        PrWorkStatus.PROPOSED,
        PrWorkStatus.ACCEPTED,
        PrWorkStatus.IN_PROGRESS,
        PrWorkStatus.COMPLETED,
    }
)

#: The statuses a **deadline** applies to.
#:
#: Narrower than :data:`OPEN_WORK_STATUSES`, and the two exclusions are the
#: whole definition of the word:
#:
#: * ``PROPOSED`` is not late, because it is not work yet. Nobody agreed to do
#:   it, so nobody is behind on it;
#: * ``COMPLETED`` is not late either. The contributor finished; what it is
#:   waiting for is a validator, and calling that the employee's debt would put
#:   a manager's queue in an employee's "Nợ việc" list.
#:
#: Both cases are still visible - as *chờ duyệt* and *chờ xác nhận* - just not
#: as overdue.
OVERDUE_STATUSES: frozenset[PrWorkStatus] = frozenset(
    {PrWorkStatus.ACCEPTED, PrWorkStatus.IN_PROGRESS}
)

#: Statuses in which a contribution may become ``COUNTED``. Exactly one.
COUNTABLE_STATUSES: frozenset[PrWorkStatus] = frozenset({PrWorkStatus.APPROVED})


def allowed_work_transitions(
    current: PrWorkStatus, *, source_derived: bool = False
) -> frozenset[PrWorkStatus]:
    """Which statuses may follow ``current`` for this kind of work.

    The same table for both, minus :data:`SOURCE_ONLY_TRANSITIONS` when the item
    is one a person filed. Subtracting rather than keeping two tables is what
    stops the manual and derived ladders drifting apart: there is one matrix,
    and one sentence about who may take each edge.
    """
    targets = WORK_TRANSITIONS[current]
    if source_derived:
        return targets
    return frozenset(
        target for target in targets if (current, target) not in SOURCE_ONLY_TRANSITIONS
    )


def can_transition_work(
    current: PrWorkStatus, target: PrWorkStatus, *, source_derived: bool = False
) -> bool:
    """True when this edge exists and this kind of work may take it."""
    return target in allowed_work_transitions(current, source_derived=source_derived)


def assert_work_transition(
    current: PrWorkStatus, target: PrWorkStatus, *, source_derived: bool = False
) -> None:
    """Raise unless the edge is legal for this kind of work.

    ``details`` carries the set that *would* have been accepted, so a client can
    offer the real next steps instead of restating the refusal - the same shape
    :func:`~meobot.domain.pr.workflow.assert_content_transition` uses.

    ``source_derived`` defaults to ``False``, which is the safe direction: every
    caller that has not thought about it gets M1's ladder exactly as M1 shipped
    it, and only a caller that has established the item came from a projector
    can reach :data:`SOURCE_ONLY_TRANSITIONS`.
    """
    if can_transition_work(current, target, source_derived=source_derived):
        return
    raise PrValidationError(
        f"Cannot move work from {current.value!r} to {target.value!r}",
        details={
            "field": "status",
            "reason": "illegal_transition",
            "current": current.value,
            "target": target.value,
            "allowed": sorted(
                status.value
                for status in allowed_work_transitions(current, source_derived=source_derived)
            ),
        },
    )


def is_overdue(status: PrWorkStatus, due_at: datetime | None, *, now: datetime) -> bool:
    """Whether this work is late, right now, regardless of any period filter.

    Deliberately takes no period. *"What is still outstanding"* and *"what did
    this person achieve in September"* are two different questions, and the
    requirement that answering the second must never hide the first is the whole
    reason this function has no date-range parameter to be given one.

    Work with no deadline is never overdue: nobody promised it for a date.
    """
    if due_at is None:
        return False
    return status in OVERDUE_STATUSES and due_at < now


# ---------------------------------------------------------------------------
# Quantity and credit
# ---------------------------------------------------------------------------

#: The largest quantity a single work item may claim.
#:
#: Bounded because ``quantity`` is the one field on a work item that scales what
#: it is worth, and an unbounded number in it is an unbounded claim. Ten
#: thousand comments in one item is already an implausible day; a hundred
#: thousand is a typo or a test of the system.
MAX_WORK_QUANTITY = Decimal("100000")

#: What one contributor's share is worth, by default and at most.
#:
#: **Always 1.0 unless a manager says otherwise, and never automatically
#: divided.** Three people on one shoot each did a shoot; splitting them into
#: 0.333 would say the department got a third of a day's work from each, which
#: is not what happened. The department's count is one *work item*; each
#: person's count is one *contribution*, and the two are separate figures for
#: exactly this reason.
#:
#: The upper bound is the anti-gaming half: a weight may be reduced to record a
#: minor share, and may never be raised above a full unit. Nobody's contribution
#: is worth two people's.
DEFAULT_CREDIT_WEIGHT = Decimal("1.0000")
MIN_CREDIT_WEIGHT = Decimal("0.0001")
MAX_CREDIT_WEIGHT = Decimal("1.0000")


# ---------------------------------------------------------------------------
# The source key contract
# ---------------------------------------------------------------------------

#: What a source key looks like: ``{source}:{entity-uuid}:{MILESTONE}``.
#:
#: The middle segment is a UUID and the last is an upper-snake milestone name.
#: Nothing parses meaning back out of a key - it is compared for equality and
#: nothing else - but the shape is validated on the way in, because a key
#: assembled by string concatenation somewhere is a key that will one day be
#: assembled wrongly and silently stop deduplicating.
SOURCE_KEY_PATTERN: re.Pattern[str] = re.compile(
    r"^(content|task|recurring|system):"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}:"
    r"[A-Z][A-Z0-9_]{2,39}$"
)

#: How long a key may be. Matches the column.
MAX_SOURCE_KEY_LENGTH = 200


def work_source_key(source_type: PrWorkSourceType, entity_id: uuid.UUID, milestone: str) -> str:
    """Compose the canonical key for one *semantic work event*.

    The contract, and the reason each half of it is the way it is:

    **The milestone is part of the key.** One content item legitimately produces
    several pieces of work - somebody wrote it, somebody edited it, somebody
    posted it - and a key of ``content:{id}`` alone would let the first of those
    exist and silently swallow the other two. So the key names the *event*, not
    the record: ``content:{id}:SCRIPT_APPROVED`` and
    ``content:{id}:PRODUCTION_APPROVED`` are two different pieces of work about
    one content item, and both may exist.

    **The transition event id is deliberately absent.** An approval that is
    undone and then re-made is *the same script being approved*, not a second
    one. Keying on the event that carried it would produce a second work item
    and double-count the writer; keying on the milestone means the projector
    finds the existing row and reconciles it. Repeated events, replayed jobs and
    a re-run backfill therefore all converge on one row, which is what "must
    never double-count" requires.

    **Manual work has no key.** Nobody can compute one for a job somebody typed
    in, and a synthetic key would be a unique constraint over a random number -
    which is no constraint at all. ``source_key`` is nullable and its unique
    index is partial; see the model.

    Args:
        source_type: Anything but :attr:`PrWorkSourceType.MANUAL`.
        entity_id: The row the work was derived from.
        milestone: The event, ``UPPER_SNAKE``. Names the *step reached*, never
            the work type - ``SCRIPT_APPROVED``, not ``SHORT_SCRIPT``.

    Raises:
        PrValidationError: The source type has no keys, or the composed key is
            not the shape :data:`SOURCE_KEY_PATTERN` describes.
    """
    if source_type is PrWorkSourceType.MANUAL:
        raise PrValidationError(
            "Manual work has no source key",
            details={"field": "source_key", "reason": "manual_has_no_key"},
        )
    key = f"{source_type.value.lower()}:{entity_id}:{milestone.strip().upper()}"
    assert_source_key(key)
    return key


def assert_source_key(key: str) -> None:
    """Raise unless ``key`` is a well-formed source key.

    Called by :func:`work_source_key` on the way out and by the service on the
    way in, so a key that arrives from anywhere - a projector, a backfill, a
    request body - is the same shape as one this module composed.
    """
    if len(key) > MAX_SOURCE_KEY_LENGTH or not SOURCE_KEY_PATTERN.match(key):
        raise PrValidationError(
            "Source key is not in the canonical format",
            details={
                "field": "source_key",
                "reason": "malformed_source_key",
                "expected": "{source}:{uuid}:{MILESTONE}",
            },
        )


# ===========================================================================
# Evidence as free text
# ===========================================================================
#
# Evidence used to be asked for as two fields - a label and a link - and on a
# phone that is one field too many. A member now types **one** text: a
# description, a Drive link, a Facebook link, several lines, any mix. The row
# underneath is unchanged: the text goes into ``note``, and the two columns the
# table still requires are *derived* from it so nothing about the schema moves.

#: The ``location`` of a text-only evidence row - one whose text names no URL.
#:
#: ``pr_work_evidence.location`` is ``NOT NULL`` with a not-empty CHECK, and
#: this patch does not change the table. A sentinel is the honest value for a
#: row whose proof *is* its text: it is not a URL, nothing renders it, and the
#: API reports such a row with ``location: null``. A row whose text contains a
#: link gets that link instead, so legacy readers of ``location`` still find
#: something to open.
EVIDENCE_TEXT_ONLY_LOCATION = "text-only"

#: The longest derived label. Matches ``pr_work_evidence.label``.
MAX_EVIDENCE_LABEL = 200

_URL = re.compile(r"https?://[^\s<>\"']+")
_TRAILING_PUNCTUATION = ".,;:!?)]}'\""


def find_urls(text: str) -> list[str]:
    """Every ``http://`` or ``https://`` link in free text, in order.

    Trailing sentence punctuation is not part of a link somebody typed at the
    end of a sentence - "xem https://a.b/c." means ``https://a.b/c`` - and the
    same rule is what the screen applies when it renders the text, so the two
    agree on what is clickable.
    """
    found: list[str] = []
    for match in _URL.finditer(text):
        url = match.group(0).rstrip(_TRAILING_PUNCTUATION)
        if url:
            found.append(url)
    return found


def evidence_label_for(text: str) -> str:
    """The derived label of a text evidence: its first non-empty line, bounded.

    Kept because ``label`` is what the history and audit rows already name an
    evidence by, and what a legacy client renders; it is never something the
    member is asked for again.
    """
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:MAX_EVIDENCE_LABEL]
    return text.strip()[:MAX_EVIDENCE_LABEL] or "Minh chứng"


def evidence_location_for(text: str) -> str:
    """The derived location: the first link in the text, else the sentinel."""
    urls = find_urls(text)
    return urls[0] if urls else EVIDENCE_TEXT_ONLY_LOCATION


__all__: list[str] = [
    "COUNTABLE_STATUSES",
    "DEFAULT_CREDIT_WEIGHT",
    "EVIDENCE_TEXT_ONLY_LOCATION",
    "MAX_CREDIT_WEIGHT",
    "MAX_EVIDENCE_LABEL",
    "MAX_SOURCE_KEY_LENGTH",
    "MAX_WORK_QUANTITY",
    "MIN_CREDIT_WEIGHT",
    "OPEN_WORK_STATUSES",
    "OVERDUE_STATUSES",
    "SOURCE_KEY_PATTERN",
    "SOURCE_ONLY_TRANSITIONS",
    "TERMINAL_WORK_STATUSES",
    "WORK_TRANSITIONS",
    "PrWorkAssignmentMode",
    "PrWorkCategory",
    "PrWorkContributionRole",
    "PrWorkCountStatus",
    "PrWorkEventType",
    "PrWorkSourceType",
    "PrWorkStatus",
    "PrWorkUnit",
    "allowed_work_transitions",
    "assert_source_key",
    "assert_work_transition",
    "can_transition_work",
    "evidence_label_for",
    "evidence_location_for",
    "find_urls",
    "is_overdue",
    "work_source_key",
]
