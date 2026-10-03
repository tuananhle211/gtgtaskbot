"""One approval grant's scope, as a ``WHERE`` clause.

Step 1F.2.7a. Step 1F.2.7 put the scope rule in
:class:`~meobot.domain.pr.grants.GrantScope`, where it decides **one item that
is already loaded**. A queue cannot work that way: "what is waiting for me" has
to be a predicate the database applies before ``LIMIT``, or the page is a page
of rows somebody then filters in Python and the pager caption is a lie.

So this module is the same rule expressed over columns. It is the *only* other
expression of it, and it is deliberately a translation rather than a
re-derivation - :func:`scope_matches` mirrors
:meth:`~meobot.domain.pr.grants.GrantScope.covers` branch for branch, in the
same order, with the same names.

Why two implementations can be trusted
--------------------------------------

Because a test refuses to let them differ.
``tests/unit/test_pr_scoped_queue.py`` builds a matrix of grant scopes and of
content items - classified and not, single-channel, multi-channel and
target-less - loads every item through this clause and evaluates
:meth:`GrantScope.covers` on the same pair, and asserts the two agree on every
cell. A branch added here and not there, or there and not here, fails it.

That is stronger than sharing code would have been, because the shapes genuinely
differ: one walks a Python object, the other has to correlate a subquery over
``pr_content_targets``. A shared abstraction thin enough to cover both would
have obscured the one thing worth reading - that the channel test is a
**subset** test.

The subset test
---------------

``NOT EXISTS (a target of this item whose channel is outside the grant)``, and
separately ``EXISTS (a target at all)``. Both halves are needed:

* without the first, a grant naming CH-0001 would decide an item going to
  CH-0001 *and* CH-0009 - approving it is what puts it on CH-0009;
* without the second, an item with **no** targets would satisfy the ``NOT
  EXISTS`` vacuously and every scoped grant would silently reach every
  unassigned item. That is the largest quiet widening this module could produce,
  and it is why *no channel* is its own branch behind
  ``include_unassigned_channel`` rather than a side effect of the arithmetic.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import ColumnElement, and_, false, or_, select, true

from meobot.application.pr_capability_service import PrCapabilityGrant
from meobot.db.models.pr import PrContentItem, PrContentTarget
from meobot.domain.pr.content_views import gate_stage_for
from meobot.domain.pr.grants import GrantScope, PrGrantScopeMode
from meobot.domain.pr.models import PrWorkflowStage


def content_type_matches(scope: GrantScope) -> ColumnElement[bool]:
    """The classification axis, as SQL.

    The twin of :meth:`GrantScope.covers_content_type`. ``false()`` when a
    ``SELECTED`` scope selected nothing - which the service refuses to store, so
    it is a backstop rather than a case, and it fails closed.
    """
    if scope.content_type_scope is PrGrantScopeMode.ALL:
        return true()
    branches: list[ColumnElement[bool]] = []
    if scope.content_types:
        branches.append(
            PrContentItem.content_type.in_(
                sorted(scope.content_types, key=lambda member: member.value)
            )
        )
    if scope.include_unclassified_content:
        branches.append(PrContentItem.content_type.is_(None))
    return or_(*branches) if branches else false()


def channels_match(scope: GrantScope) -> ColumnElement[bool]:
    """The channel axis, as SQL. A **subset** test - see the module docstring.

    The twin of :meth:`GrantScope.covers_channels`. Both subqueries correlate on
    ``PrContentItem.id``, so this composes into any statement that already
    selects from ``pr_content_items`` and needs no join of its own.
    """
    if scope.channel_scope is PrGrantScopeMode.ALL:
        return true()

    targets = select(PrContentTarget.id).where(PrContentTarget.content_id == PrContentItem.id)
    has_target = targets.exists()

    branches: list[ColumnElement[bool]] = []
    if scope.channel_ids:
        outside = targets.where(
            PrContentTarget.channel_id.not_in(sorted(scope.channel_ids, key=str))
        ).exists()
        branches.append(and_(has_target, ~outside))
    if scope.include_unassigned_channel:
        branches.append(~has_target)
    return or_(*branches) if branches else false()


def scope_matches(scope: GrantScope) -> ColumnElement[bool]:
    """Both axes conjoined: the SQL twin of :meth:`GrantScope.covers`."""
    return and_(content_type_matches(scope), channels_match(scope))


def approvable_by(grants: Sequence[PrCapabilityGrant]) -> ColumnElement[bool]:
    """Items one person may currently decide, from the grants that authorise them.

    A disjunction over the grants, each one contributing *its own gate and its
    own scope* - so somebody holding Team Lead over Facebook posts and Internal
    Review over everything sees exactly those two slices, and holding two grants
    of one gate unions their scopes the way the write path does.

    ``grants`` must already be the **effective** ones: active on the day, not
    revoked, and past the role-baseline test where the grant asks for it. That
    filtering is
    :meth:`~meobot.application.pr_capability_service.PrCapabilityService.approval_grants_for`'s
    job, because it is the same question the write asks and there must not be a
    second answer to it.

    ``false()`` for an empty sequence - an empty queue, which is the honest
    answer for somebody who may approve nothing.
    """
    clauses: list[ColumnElement[bool]] = []
    stages: list[PrWorkflowStage] = []
    for grant in grants:
        stage = gate_stage_for(grant.capability)
        if stage is None:
            # Not a review gate, so it puts nothing in a review queue. Reached
            # only if a non-gate capability ever became grant-backed.
            continue
        if stage not in stages:
            stages.append(stage)
        clauses.append(and_(PrContentItem.workflow_stage == stage, scope_matches(grant.scope)))
    if not clauses:
        return false()
    # The leading ``IN`` is **redundant** - every disjunct already pins the
    # stage - and it is there for the planner. Without it the whole predicate is
    # an ``OR`` of correlated ``EXISTS``, which PostgreSQL will happily answer
    # with a sequential scan; with it there is one sargable term over
    # ``ix_pr_content_items_workflow_stage``, and the scope subqueries run only
    # against the handful of rows standing at a gate. Equivalent by
    # construction, so it cannot widen the result.
    return and_(PrContentItem.workflow_stage.in_(stages), or_(*clauses))


__all__: list[str] = [
    "approvable_by",
    "channels_match",
    "content_type_matches",
    "scope_matches",
]
