"""Period containers and their results: the vocabulary and the one comparison.

The rule this module protects, stated once so every reader has the same
sentence in mind::

    WORK = actual work performed.   KPI = target only.

A period container is an ordinary :class:`~meobot.db.models.pr_work.PrWorkItem`
whose ``reporting_period_id`` and ``subject_user_id`` are set - **one stream, one
employee, one month**. Results accumulate inside it, and the container's
``quantity`` is the sum of the results a validator counted. A KPI target is read
beside that sum for comparison and nothing else: it never decides whether a
result may be reported, never caps the actual and never caps what the actual is
worth.

This module knows nothing about SQL, HTTP or Telegram. It holds the enums, the
bounds and :func:`compare_to_target`, which is the only place ``actual / target``
is computed - the KPI screen, the work card and the performance breakdown all
call it rather than restating the division.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from meobot.domain.pr.errors import PrValidationError
from meobot.domain.pr.work import MAX_WORK_QUANTITY


class PrWorkResultSource(StrEnum):
    """Where one result came from.

    ``MANUAL`` is a person declaring a result; every other value is a module
    that contributed one and carries a ``source_key`` so contributing it twice
    is impossible rather than merely unlikely. ``SEEDING``, ``CRM`` and ``OTHER``
    are declared now so the enum is the seam a future module plugs into; today
    only ``MANUAL`` and ``CONTENT`` are written.
    """

    MANUAL = "MANUAL"
    CONTENT = "CONTENT"
    SEEDING = "SEEDING"
    CRM = "CRM"
    SYSTEM = "SYSTEM"
    OTHER = "OTHER"
    #: The Ads order engine: a production node a Leader approved. Keyed
    #: ``order:<node uuid>:<NODE_TYPE>`` by
    #: :mod:`meobot.application.orders.work_recorder`.
    ORDER = "ORDER"


class PrWorkExclusionKind(StrEnum):
    """**Why** an ``EXCLUDED`` result is out. Migration ``0041``.

    ``EXCLUDED`` alone says a result does not count; it does not say whether
    the projector may put it back. That is the question every convergence path
    - the worker, *Đồng bộ lại từ Nội dung*, *Đồng bộ thiếu*, *Xây dựng lại* -
    has to answer, and the answer differs by who decided:

    * ``ADMIN_REMOVED`` - an administrator clicked *Xóa kết quả*. Accounting
      was taken out; the content source was not touched, and the next
      canonical projection **re-evaluates** it against current source truth.
    * ``VALIDATOR_REJECTED`` - a validator clicked *Từ chối / Không ghi nhận*.
      A reviewed decision. **No projection may reverse it**; only *Xem xét lại*
      (``PR_WORK_VALIDATE``) releases it back to ``PENDING``.
    * ``SOURCE_REVERSED`` - the source no longer supports the result: an
      approval withdrawn, a mapping no longer pointing at this type. Current
      source truth decides whether it returns.

    A row excluded before ``0041`` carries ``None``: its reason was free text
    and is not guessed. See :func:`source_may_restore` for what ``None`` means
    to the projector.
    """

    ADMIN_REMOVED = "ADMIN_REMOVED"
    VALIDATOR_REJECTED = "VALIDATOR_REJECTED"
    SOURCE_REVERSED = "SOURCE_REVERSED"


#: The kinds a source-driven projection may re-evaluate. **The one convergence
#: policy** - every projector path asks :func:`source_may_restore` and nothing
#: else, so the rule cannot drift between the worker and a button.
SOURCE_RESTORABLE_KINDS: frozenset[PrWorkExclusionKind] = frozenset(
    {PrWorkExclusionKind.ADMIN_REMOVED, PrWorkExclusionKind.SOURCE_REVERSED}
)

#: The kinds *Xem xét lại* releases. A validator's own decision, and a legacy
#: exclusion whose author the row cannot name.
RECONSIDERABLE_KINDS: frozenset[PrWorkExclusionKind | None] = frozenset(
    {PrWorkExclusionKind.VALIDATOR_REJECTED, None}
)


def source_may_restore(kind: PrWorkExclusionKind | None) -> bool:
    """May a canonical projection turn this exclusion back into ``PENDING`` / ``COUNTED``?

    ``True`` for an administrative removal and a source reversal - both are
    "the ledger against current source truth" questions and the projector is
    the one that answers them. ``False`` for a validator's rejection, which is
    a person's reviewed decision, and for a **legacy** ``None``: the system
    never silently reverses a human decision it cannot attribute. Both are
    released only by an explicit *Xem xét lại*.
    """
    return kind in SOURCE_RESTORABLE_KINDS


def may_reconsider(kind: PrWorkExclusionKind | None) -> bool:
    """Is *Xem xét lại* the right release for this exclusion?"""
    return kind in RECONSIDERABLE_KINDS


class PrWorkCountOrigin(StrEnum):
    """**Who** counted a source-derived result. Two validation layers, named.

    A content milestone can be counted two ways, and the distinction is what
    a projection replay needs in order not to undo a person:

    * ``SOURCE`` - the source supplied an independent validator (the head who
      approved the script, the reviewer who accepted the cut) and the
      projector counted the row on that person's instant. The count *is* the
      source's validation, so when the source withdraws it the count goes;
    * ``WORK_VALIDATOR`` - a person holding ``PR_WORK_VALIDATE`` counted the
      row in the Work module, because the source could not validate it
      independently (a self-approved script, a cut nobody has accepted). The
      count is a **Work decision**: the source never made it and a replay of
      the source cannot take it back while the milestone still qualifies.

    Written into the ``RESULT_COUNTED`` / ``COUNTED`` history row at count
    time as ``origin`` and read back from there - the timeline is the one
    place both counting paths already write, in the same transaction.
    """

    SOURCE = "SOURCE"
    WORK_VALIDATOR = "WORK_VALIDATOR"


def source_may_reverse_count(
    *,
    source_eligible: bool,
    source_independent: bool,
    origin: PrWorkCountOrigin | None,
) -> bool:
    """**The one convergence rule for a COUNTED source-derived row.**

    Every path that replays a source against a counted row - the worker, the
    single-content button, the batch sync, the rebuild, and the legacy
    one-off item path - asks this and nothing else, so the answer cannot
    drift between them:

    * the source no longer qualifies (the milestone is gone) -> **reverse**,
      whoever counted it. A person's confirmation does not keep work alive
      that the department has withdrawn;
    * the source still qualifies and still supplies an independent validator
      -> **keep**. Nothing changed;
    * the source still qualifies but its own validation is not independent
      (withdrawn acceptance, self-approval) -> reverse only a count the
      **source** made. A count a Work validator made stands: "the source did
      not validate this independently" is not "the source no longer
      supports this", and the person who confirmed it is the independent
      validation the row was waiting for. An unattributable count (``None``,
      a row with no counting event on record) is treated as the source's:
      the projector never guesses a person into existence.
    """
    if not source_eligible:
        return True
    if source_independent:
        return False
    return origin is not PrWorkCountOrigin.WORK_VALIDATOR


#: The longest label a result may carry. Matches the column.
MAX_RESULT_LABEL = 200
#: The longest link or note. Matches ``PrWorkEvidence.location``'s service bound.
MAX_RESULT_LINK = 2000
MAX_RESULT_NOTE = 4000
#: The default a result form starts at: one unit. Most streams - a script, a
#: customer, an order - are reported one at a time.
DEFAULT_RESULT_QUANTITY = Decimal("1")

#: The reason code a lifecycle action carries when it is refused because the
#: item is a period container rather than a one-off job.
PERIOD_CONTAINER_LOCKED = "period_container"
#: A container found ``CANCELLED`` - a state no current path produces and one
#: that would split the actual between the card and the KPI screen. Repaired
#: by an administrator, never written into.
PERIOD_CONTAINER_CANCELLED = "period_container_cancelled"
#: The subject's month has a finalised performance figure: no accounting
#: mutation may move the actual under it.
PERFORMANCE_FINALIZED = "performance_finalized"

PERCENT_QUANTUM = Decimal("0.1")


def require_result_quantity(value: Decimal | None) -> Decimal:
    """A positive result quantity within :data:`MAX_WORK_QUANTITY`.

    Zero and negative are refused: a report of nothing is not a report, and a
    correction is an exclusion rather than a negative row - the ledger keeps
    what was declared and records who took it back.
    """
    if value is None:
        return DEFAULT_RESULT_QUANTITY
    if value <= 0:
        raise PrValidationError(
            "Số lượng kết quả phải lớn hơn 0.",
            details={"field": "quantity", "reason": "out_of_range"},
        )
    if value > MAX_WORK_QUANTITY:
        raise PrValidationError(
            f"Số lượng kết quả không vượt quá {MAX_WORK_QUANTITY:,.0f}.",
            details={"field": "quantity", "reason": "out_of_range"},
        )
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass(frozen=True, slots=True)
class TargetComparison:
    """What the actual looks like against a target, or against no target.

    ``completion_percent`` is **uncapped**: 27 against 20 is ``135.0``, and a
    progress bar that stops at 100 is the screen's business, not this
    object's. It is ``None`` - not zero - when there is no target, because
    "no KPI" and "0 % of the KPI" are different sentences and the second one
    would read as a failure.
    """

    actual: Decimal
    target: Decimal | None
    completion_percent: Decimal | None
    over_target: Decimal
    remaining: Decimal

    @property
    def has_target(self) -> bool:
        return self.target is not None

    @property
    def is_met(self) -> bool:
        return self.target is not None and self.actual >= self.target


def compare_to_target(actual: Decimal, target: Decimal | None) -> TargetComparison:
    """**The** KPI comparison. Actual against target, never the other way round.

    Pure, and deliberately the only arithmetic in this module:

    * ``completion_percent = actual / target * 100`` rounded half-up to one
      decimal, ``None`` when there is no target or the target is zero;
    * ``over_target = max(actual - target, 0)``;
    * ``remaining = max(target - actual, 0)``.

    Nothing here caps the actual. The target is a number to compare against.
    """
    actual = Decimal(actual)
    if target is None or target <= 0:
        return TargetComparison(
            actual=actual,
            target=None,
            completion_percent=None,
            over_target=Decimal("0"),
            remaining=Decimal("0"),
        )
    target = Decimal(target)
    percent = (actual / target * Decimal("100")).quantize(PERCENT_QUANTUM, rounding=ROUND_HALF_UP)
    return TargetComparison(
        actual=actual,
        target=target,
        completion_percent=percent,
        over_target=max(actual - target, Decimal("0")),
        remaining=max(target - actual, Decimal("0")),
    )


__all__: list[str] = [
    "DEFAULT_RESULT_QUANTITY",
    "MAX_RESULT_LABEL",
    "MAX_RESULT_LINK",
    "MAX_RESULT_NOTE",
    "PERFORMANCE_FINALIZED",
    "PERIOD_CONTAINER_CANCELLED",
    "PERIOD_CONTAINER_LOCKED",
    "RECONSIDERABLE_KINDS",
    "SOURCE_RESTORABLE_KINDS",
    "PrWorkCountOrigin",
    "PrWorkExclusionKind",
    "PrWorkResultSource",
    "TargetComparison",
    "compare_to_target",
    "may_reconsider",
    "require_result_quantity",
    "source_may_restore",
    "source_may_reverse_count",
]
