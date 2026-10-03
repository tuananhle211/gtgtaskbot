"""When two channel assignments are the same person doing the same job twice.

The database enforces one narrow rule - at most one **open** row per
``(channel_id, user_id, assignment_role)``, via the partial unique index
``uq_pr_channel_assignments_open_role``. Step 1A documented, and
``tests/integration/test_pr_core_migrations.py`` asserts, exactly what that
leaves accepted: two *closed* rows whose dates overlap, and a closed row
overlapping an open one. Rejecting those was deferred to the service layer,
which is here.

Interval semantics: **closed**, ``[effective_from, effective_to]``
------------------------------------------------------------------

``effective_to`` is the **last day the assignment is in force**, not the first
day it is not. Two consequences, and both are chosen rather than inherited:

* A row with ``effective_from == effective_to`` covers exactly one day. That is
  already what the schema means - the ``CHECK`` permits the pair and
  ``test_an_assignment_may_start_and_end_on_the_same_day`` calls it "a cover
  for somebody on leave". Half-open ``[from, to)`` semantics would make that
  same row cover *nothing*, silently reinterpreting rows already written.
* A handover therefore cannot share a day. ``[Jan 1, Jun 30]`` and
  ``[Jun 30, Dec 31]`` **overlap** on the 30th and are refused; the outgoing
  row must end on the 29th. This is stricter than half-open semantics and is
  the point: on the 30th, under closed reading, two people would both be the
  channel owner, and "who owned this channel on the 30th" is precisely the
  question the dated shape exists to answer.

``effective_to IS NULL`` means open-ended - in force from ``effective_from``
with no planned end. Two open-ended rows for the same triple always overlap,
which is the case the database already catches; everything else is caught
here.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from typing import Protocol


class AssignmentPeriod(Protocol):
    """The two columns overlap is decided from.

    A protocol rather than the ORM class so the rule can be exercised against
    plain values, and so this module does not import the persistence layer.
    """

    @property
    def effective_from(self) -> date: ...

    @property
    def effective_to(self) -> date | None: ...


def periods_overlap(
    first_from: date,
    first_to: date | None,
    second_from: date,
    second_to: date | None,
) -> bool:
    """True when two closed, possibly open-ended day ranges share a day.

    ``None`` for either end date means "no planned end", which participates in
    every comparison as an unbounded upper limit.
    """
    if first_to is not None and first_to < second_from:
        return False
    return not (second_to is not None and second_to < first_from)


def first_overlapping(
    candidates: Iterable[AssignmentPeriod],
    *,
    effective_from: date,
    effective_to: date | None,
) -> AssignmentPeriod | None:
    """The first existing period clashing with the proposed one, if any.

    The caller has already narrowed ``candidates`` to the same channel, person
    and role - overlap only means something within one triple, and two people
    holding the same role over the same dates is a deliberate, legal
    arrangement rather than a clash.
    """
    for existing in candidates:
        if periods_overlap(
            existing.effective_from, existing.effective_to, effective_from, effective_to
        ):
            return existing
    return None


__all__: list[str] = ["AssignmentPeriod", "first_overlapping", "periods_overlap"]
