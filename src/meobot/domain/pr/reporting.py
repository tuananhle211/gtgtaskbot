"""Stored vocabulary for PR reporting: periods, metrics, issues and report runs.

Step 1B's half of the PR module's vocabulary. The same rule as
:mod:`meobot.domain.pr.models` applies to every enum here: the member name and
the member value are the same uppercase code, because these values are written
to the database through :func:`~meobot.db.base.value_enum`, which stores the
*value*. Name and value being identical means the string in the column, the
string in migration 0013 and the string in Python are one string, so a rename
fails the enum-parity test rather than quietly writing an unrecognised code.

Step 1B **stores** these codes. It does not interpret them: nothing here knows
that ``GENERATED`` follows ``GENERATING``, that a ``LOCKED`` period refuses new
weekly input, or that an ``APPROVED`` weekly row must not be edited. Those are
Step 1C service rules, and they belong in a service that can be tested against a
transition table rather than in a column.
"""

from __future__ import annotations

from enum import StrEnum


class PrPeriodType(StrEnum):
    """Whether a reporting period covers a week or a month.

    Two separate report cadences, not one with a duration: a weekly management
    report and a monthly one ask different questions of the same data, and a
    month is not four weeks.
    """

    WEEK = "WEEK"
    MONTH = "MONTH"


class PrPeriodStatus(StrEnum):
    """How far a reporting period has been put beyond further change.

    Three states rather than a boolean, because ``CLOSED`` and ``LOCKED`` are
    genuinely different: a closed period has had its numbers agreed and can
    still be corrected if somebody finds a mistake, while a locked one has been
    reported to management and must not move underneath the report that quotes
    it. Enforcement of both is a Step 1C service rule - the database stores the
    state and the timestamp, and refuses nothing on the strength of them.
    """

    OPEN = "OPEN"
    CLOSED = "CLOSED"
    LOCKED = "LOCKED"


class PrPublicationStatus(StrEnum):
    """Whether a publication is still where it was published.

    ``REMOVED`` is a decision somebody made - taken down, deleted, retracted.
    ``UNAVAILABLE`` is an observation - the link no longer resolves and nobody
    knows why. Collapsing the two would lose exactly the distinction a report
    has to explain. Neither is a deletion: the row and its metric history stay.

    ``REVERSED`` is Step 1F.2.3f.1 and is a fourth kind of thing entirely. The
    first three are all claims **about the post**: it is up, it was taken down,
    it cannot be reached. ``REVERSED`` is a claim about the **record**: this row
    should not have been written - wrong channel, wrong cut, wrong link, entered
    by mistake - and what it describes never happened.

    That is why it could not reuse ``REMOVED``. "We published it and then took it
    down" and "we never published it and I typed this in by accident" are
    different facts, and a report that could not tell them apart would count a
    mistyped row as a real posting that was later pulled.

    The row still stays. Publication history is operational evidence and a
    correction is not an erasure - see
    :meth:`~meobot.application.pr_publication_service.PrPublicationService.reverse_publication`.
    """

    PUBLISHED = "PUBLISHED"
    REMOVED = "REMOVED"
    UNAVAILABLE = "UNAVAILABLE"
    #: Step 1F.2.3f.1. Recorded in error and taken back. Never counted as a
    #: publication that happened - see :data:`ACTIVE_PUBLICATION_STATUSES`.
    REVERSED = "REVERSED"


#: The statuses that still assert *"this went out"*. Step 1F.2.3f.1.
#:
#: **Derived rather than listed**, and the direction matters: a fifth status
#: added later is active until somebody says otherwise, which is the safe
#: default for a set that gates whether content may be un-published. Only
#: ``REVERSED`` is excluded, because only ``REVERSED`` says the posting never
#: happened.
#:
#: ``REMOVED`` and ``UNAVAILABLE`` are deliberately **in** it. A post that was
#: taken down still went out - people saw it, and the numbers hanging off it are
#: real - so a piece whose only publication was removed must not become
#: *Sẵn sàng đăng* again. That is the distinction this set exists to keep.
ACTIVE_PUBLICATION_STATUSES: frozenset[PrPublicationStatus] = frozenset(
    status for status in PrPublicationStatus if status is not PrPublicationStatus.REVERSED
)


def is_active_publication(status: PrPublicationStatus) -> bool:
    """Whether this publication still counts as something that was published.

    The **one** answer to that question. Step 1F.2.3f.1 exists partly because
    ``status != REVERSED`` written in four services is four places for the fifth
    status to be classified differently - see
    :data:`ACTIVE_PUBLICATION_STATUSES`.

    Note what deliberately does **not** call it: the permanent-delete rule and
    the derivative-delete rule both count *every* publication, reversed
    included. A piece that has ever had a publication row has been through the
    act of being published, and an undo must not become a way to earn the right
    to destroy the evidence.
    """
    return status in ACTIVE_PUBLICATION_STATUSES


class PrMetricSource(StrEnum):
    """Where one metric observation came from.

    Part of the uniqueness key on both snapshot tables on purpose: the same
    publication at the same instant can legitimately have an API number and a
    hand-typed number, and they can disagree. Storing both is what makes the
    disagreement visible; storing one would make it a silent overwrite.
    """

    API = "API"
    MANUAL = "MANUAL"
    IMPORT = "IMPORT"


class PrWeeklyInputStatus(StrEnum):
    """How far one version of a week's manually entered numbers has got.

    A version is never edited once it reaches ``APPROVED`` or ``LOCKED`` - a
    correction is a new version with a higher ``version_no``. The database
    stores the status and enforces the version uniqueness; refusing the in-place
    update is a Step 1C service rule.
    """

    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    LOCKED = "LOCKED"


class PrIssueSeverity(StrEnum):
    """How much an issue matters.

    A judgement recorded at the time, not derived from the numbers. What counts
    as ``CRITICAL`` changes with the quarter, and a value computed from a
    threshold would rewrite history every time the threshold moved.
    """

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class PrIssueStatus(StrEnum):
    """Where an issue has got to.

    ``CANCELLED`` is distinct from ``RESOLVED``: an issue that turned out not to
    be one was never fixed, and a report that counts it as resolved is lying
    about the team's throughput.
    """

    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    RESOLVED = "RESOLVED"
    CANCELLED = "CANCELLED"


class PrActionStatus(StrEnum):
    """Where one action attached to an issue has got to.

    Deliberately the same shape as :class:`~meobot.domain.pr.models.PrTaskStatus`
    minus its review states: an action is agreed in a meeting and either happens
    or does not, and it has no draft to send back for revision.
    """

    TODO = "TODO"
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"
    DONE = "DONE"
    CANCELLED = "CANCELLED"


class PrReportType(StrEnum):
    """Which periodic report a run is producing."""

    WEEKLY_MANAGEMENT = "WEEKLY_MANAGEMENT"
    MONTHLY_MANAGEMENT = "MONTHLY_MANAGEMENT"


class PrReportTriggerType(StrEnum):
    """What caused a report run to be attempted.

    ``RETRY`` is a first-class trigger rather than a flag on a manual run,
    because "how often does this need retrying" is a question about the
    generator, and it is unanswerable if a retry is indistinguishable from
    somebody pressing the button twice.
    """

    MANUAL = "MANUAL"
    SCHEDULED = "SCHEDULED"
    RETRY = "RETRY"


class PrReportRunStatus(StrEnum):
    """Where one report run has got to.

    Validation is a state of its own, before generation, because a report built
    from data that failed validation is worse than no report: it looks
    authoritative. ``VALIDATION_FAILED`` and ``FAILED`` are separate for the
    same reason ``REMOVED`` and ``UNAVAILABLE`` are - one means the numbers were
    not good enough, the other means the machinery broke.

    Step 1B stores any of these values in any order. Which transition is legal
    is a Step 1C service rule; see ``docs/pr/STEP_1B_REPORTING_DATA_FOUNDATION.md``.
    """

    PENDING = "PENDING"
    VALIDATING = "VALIDATING"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    GENERATING = "GENERATING"
    GENERATED = "GENERATED"
    DELIVERING = "DELIVERING"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"


class PrArtifactFormat(StrEnum):
    """The file format of one artifact produced by a report run.

    Part of the uniqueness key with ``version_no``, so one run may produce an
    XLSX and a PDF of the same version without either displacing the other.
    """

    XLSX = "XLSX"
    PDF = "PDF"
