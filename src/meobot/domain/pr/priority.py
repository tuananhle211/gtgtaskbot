"""How urgent, as a number the database can sort by.

Step 1F.2.3d. :class:`~meobot.domain.pr.models.PrPriority` says *which* levels
exist and in what order; this module is the one place that order becomes a
**rank**, because the column it is stored in cannot be ordered by directly.

Why a rank exists at all
------------------------

``priority`` is a ``VARCHAR`` holding the enum's own value, so ``ORDER BY
priority`` is alphabetical: ``CRITICAL, HIGH, NORMAL, URGENT``. That is not a
near-miss ordering, it is a meaningless one - *Bình thường* would sort above
*Gấp* - so the work queue has to order by something else. The options were a
stored integer column beside the string, or an expression derived from the enum.

This module is the second, and the reason is the rule
:class:`~meobot.domain.pr.models.PrWorkflowStage` already states for the
workflow: a second copy of an order is a second thing to keep in step. A
``priority_rank`` column would have to be written on every insert, backfilled on
every enum change, and would silently disagree with :data:`PRIORITY_RANK` the
first time somebody forgot. The ``CASE`` this builds is generated *from* the
enum on every query, so the two cannot drift - there is only one of them.

The cost is that the sort key is an expression rather than an indexable column.
At this module's scale that is the right trade: see
``docs/pr/STEP_1F23D_NOTIFICATIONS_AND_PRIORITY.md`` for the index decision.

One rank, both ends
-------------------

:data:`PRIORITY_RANK` is what the server sorts by and what the panel would need
if it ever sorted anything. It does not, and that is the contract: **ordering is
decided once, in SQL, before ``LIMIT``**. A browser that re-sorted the page it
was handed would disagree with the pagination it was handed alongside it, which
is how a *Rất gấp* item ends up stranded on page three - the failure Step
1F.2.3c1 fixed for workflow groups and this step must not reintroduce.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from meobot.domain.pr.models import PrPriority

#: Urgency as an integer, ``NORMAL`` lowest. Derived from the enum's declaration
#: order rather than written out, so inserting a level between two others is one
#: edit in :class:`~meobot.domain.pr.models.PrPriority` and nothing here.
#:
#: The numbers themselves are not stored anywhere and carry no meaning beyond
#: their order - only ``<`` and ``>`` between them are ever asked.
PRIORITY_RANK: Mapping[PrPriority, int] = MappingProxyType(
    {priority: rank for rank, priority in enumerate(PrPriority)}
)

#: Most urgent first. What a queue sorted by priority looks like, and the order
#: a *filter* offers its options in - "Rất gấp" is the one somebody scanning the
#: list is looking for, so it goes at the top.
#:
#: Deliberately **not** the order a *create form* offers: that one leads with the
#: default. See ``PRIORITY_ORDER`` in ``frontend/src/lib/labels.ts``.
PRIORITY_BY_URGENCY: tuple[PrPriority, ...] = tuple(reversed(list(PrPriority)))


def priority_rank(priority: PrPriority) -> int:
    """This level's sort key. Higher is more urgent."""
    return PRIORITY_RANK[priority]


__all__: list[str] = ["PRIORITY_BY_URGENCY", "PRIORITY_RANK", "priority_rank"]
