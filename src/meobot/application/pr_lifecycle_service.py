"""Permanently deleting a content item, and everything that belongs to it.

Step 1F.2.3a. The button says **"Xóa nội dung"** and it now means what the word
means: the item and every record that exists only because of it are removed from
the database, in one transaction, unrecoverably.

This replaces Step 1F.2.3's soft delete, which set ``deleted_at``, cancelled the
item and left every child row in place. That was defensible engineering and the
wrong product: a workspace that says "deleted" and keeps the row is a workspace
where nobody believes the list, and the team asked for the drafts, the reviews
and the production files of an abandoned piece to actually go.

What survives a deletion is one audit row and nothing else - see *The audit
trail* below.

The rule
--------

:func:`~meobot.domain.pr.lifecycle.may_hard_delete`, evaluated here against
facts this service gathers:

* everybody needs ``PR_CONTENT_DELETE``;
* **nobody** may delete ``PUBLISHED``, ``MEASURED`` or ``ARCHIVED`` content, and
  there is no capability, role or flag that changes that;
* management - ``PR_CONTENT_CANCEL``, i.e. whoever may already end this piece of
  work - deletes anything below that floor, including content in production;
* a member deletes only what they are responsible for, and only while it has
  never been produced.

Why the aggregate is deleted here rather than by ``ON DELETE CASCADE``
----------------------------------------------------------------------

Because deletion is a **business decision with a lifecycle condition**, and a
cascade is a property of the schema that applies to every delete anybody ever
writes. Turning this module's foreign keys into cascades would mean:

* a stray ``DELETE`` in a repair script silently destroys approvals and metrics
  that Step 1A made ``RESTRICT`` on purpose;
* the "published content is never deleted" rule would be enforced in exactly one
  place - this service - with nothing behind it, instead of by a wall of
  ``RESTRICT`` constraints that fail loudly if this service is ever wrong;
* the order and the extent of the delete would stop being reviewable, because it
  would not be written down anywhere.

So the constraints stay ``RESTRICT``, this service enumerates the aggregate in
dependency order, and the database is the backstop: if this list is ever
incomplete, the transaction fails with an integrity error and rolls back rather
than half-deleting a content item. ``tests/unit/test_pr_permanent_delete.py``
walks the metadata's own foreign keys to prove the list is complete, so the
enumeration below cannot fall behind a new table without a test failing.

The order
---------

Derived from the real foreign keys, children first:

```
pr_content_work_projections     -> content_id
pr_content_transition_events    -> content_id, version, approval, submission,
                                   and itself
pr_ai_review_run_policy_packs   -> run_id
pr_ai_review_runs               -> content_id, content_version_id, review_id
pr_ai_reviews                   -> content_id, task_id
pr_approval_events              -> content_id, task_id, production_submission_id
pr_production_submissions       -> content_id, content_version_id
pr_task_assignments             -> task_id
pr_tasks                        -> content_id
pr_content_targets              -> content_id
pr_content_resources            -> content_id
pr_content_destinations         -> content_id
pr_content_versions             -> content_id
pr_content_comments             -> content_id, and itself
pr_content_items
```

Three orderings in there are not obvious and are load-bearing: the transition
history goes **first**, because every row of it points at the approval, the
submission and the draft that the later steps remove; runs are deleted **before**
the reviews they point at through ``review_id``; and approvals **before** the
production submissions and tasks they point at.

Two tables point at *themselves* - the transition history, where a reversal names
the transition it takes back, and Step 1F.2.3g's comments, where a reply names
its root. Both are removed in **one** statement each, and that is safe rather
than lucky: PostgreSQL applies a ``RESTRICT`` check against the rows still
standing at the end of the statement, so a ``DELETE`` taking a row and everything
pointing at it in one pass leaves nothing dangling. What it refuses is a
statement that removes the parent and leaves the child, which neither predicate
here can produce - both match by ``content_id``, and both halves of each pair
belong to the same content item.

Three tables reference content and are **not** in that list, each for its own
reason:

* ``pr_publications`` (and the metric snapshots hanging off it) is the record
  that something went out. Its rows exist only for ``PUBLISHED`` content, which
  this operation refuses anyway - so rather than carry a destructive branch that
  should be unreachable, :meth:`_require_deletable` **refuses outright** when a
  publication exists. That is the same boundary as the stage check, asked of the
  data instead of the column, and it means no path through this service can
  destroy a publication or a metric;
* ``pr_work_items`` (M3) links back by ``source_key`` **text**, not by a foreign
  key, so no ``RESTRICT`` would fire and the rows would simply be orphaned. Work
  projected out of a content item is a ledger record with meaning of its own and
  a month's KPI may already be counted against it, so - exactly as with a
  publication - :meth:`_require_deletable` **refuses outright** when any exists.
  Reversal is the operator's path and it is a decision: undo the source
  milestone, let the projector take the work back out with its own audit trail,
  then delete. That keeps a KPI change explicit rather than hidden inside a
  deletion;
* ``pr_issues.content_id`` is **nullable**, and an issue belongs to a weekly
  reporting period rather than to the content it mentions. Deleting a draft must
  not delete a line out of a report somebody wrote, so the link is set to
  ``NULL`` and the issue - with its ``pr_actions`` - survives, now describing a
  problem without pointing at a row that is gone.

What is deliberately **not** touched at all: users, brands, channels, platforms,
reporting periods, and every row of the platform-policy chain - sources,
snapshots, packs and rules. Those are shared master data that outlive any one
piece of content. Only the *association* rows a run made to a pack go.

The audit trail
---------------

``audit_logs.entity_id`` is a plain ``String(200)`` with no foreign key, and
``actor_user_id`` points at ``users``, which this never touches. So one row
survives the aggregate: ``pr.content.deleted_permanently``, carrying the id as
text, the code, the title, the stage it was at, the reason, and a count of what
was removed. That is the whole trace, and it is written **before** the deletes so
that a foreign key failure rolls it back with them - an audit row describing a
deletion that did not happen would be worse than none.

Deliberately not in it: the script text, the AI findings, the approval comments.
The point of the operation is that the content is gone; copying it into the audit
table would keep it, in a place with no access control of its own.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import ColumnElement, Select, delete, exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.pr_capability_service import PrCapabilityService
from meobot.application.pr_content_query import responsible_for
from meobot.application.pr_support import record_pr_event
from meobot.application.pr_workflow_service import PrContentWorkflowService
from meobot.core.logging import get_logger
from meobot.core.time import utcnow
from meobot.db.base import Base
from meobot.db.models.pr import (
    PrApprovalEvent,
    PrContentItem,
    PrContentTarget,
    PrTask,
    PrTaskAssignment,
)
from meobot.db.models.pr_ai_review import PrAiReview
from meobot.db.models.pr_ai_review_run import PrAiReviewRun
from meobot.db.models.pr_content_asset import PrContentDerivative, PrContentDestination
from meobot.db.models.pr_content_comment import PrContentComment
from meobot.db.models.pr_content_resource import PrContentResource
from meobot.db.models.pr_content_version import PrContentVersion
from meobot.db.models.pr_content_work import PrContentWorkProjection
from meobot.db.models.pr_platform_policy import PrAiReviewRunPolicyPack
from meobot.db.models.pr_production import PrProductionSubmission
from meobot.db.models.pr_reporting import PrIssue, PrPublication
from meobot.db.models.pr_transition import PrContentTransitionEvent
from meobot.db.models.pr_work import PrWorkItem
from meobot.db.models.pr_work_result import PrWorkResult
from meobot.domain.audit.models import AuditAction
from meobot.domain.identity.models import Actor
from meobot.domain.pr.errors import (
    PrContentHasRecordedWorkError,
    PrPermissionDeniedError,
    PrPublishedContentError,
)
from meobot.domain.pr.lifecycle import (
    has_reached_production,
    is_published_onward,
    may_hard_delete,
)
from meobot.domain.pr.policy import PrCapability

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class DeletionReceipt:
    """What one permanent deletion removed.

    Returned so a caller can log or report it, and carried into the audit row.
    Counts rather than rows: the rows are gone, and a receipt that held them
    would be the copy this operation exists to avoid.
    """

    content_id: uuid.UUID
    content_code: str
    title: str
    stage_at_delete: str
    #: ``table name -> rows removed``, only for tables that had any. Empty for a
    #: bare idea with a single draft, which is the common case.
    removed: dict[str, int]

    @property
    def total_rows(self) -> int:
        return sum(self.removed.values()) + 1


class PrContentLifecycleService:
    """Permanent deletion of a content aggregate.

    Args:
        session: Unit of work. The caller owns the transaction boundary, so the
            audit row and every delete commit together or not at all. **Nothing
            here commits.**
        audit: Event writer sharing that session.
        workflow: Supplies the row lock. The content row is locked for the whole
            operation, which is what serialises a delete against a concurrent
            approval, production submission or transition - each of those locks
            the same row before it writes.
        capabilities: Resolves the two capabilities the rule is written over.
    """

    def __init__(
        self,
        session: AsyncSession,
        audit: AuditService,
        workflow: PrContentWorkflowService,
        capabilities: PrCapabilityService,
    ) -> None:
        self._session = session
        self._audit = audit
        self._workflow = workflow
        self._capabilities = capabilities

    async def delete_content(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        content_id: uuid.UUID,
        reason: str | None = None,
    ) -> DeletionReceipt:
        """Remove one content item and its whole aggregate, permanently.

        The order of operations is the safety property:

        1. **lock** the content row - so a concurrent approval or submission
           either happens entirely before this or fails to find its content
           afterwards, and never lands in between;
        2. **authorize**, against the row as locked;
        3. **audit**, before anything is destroyed;
        4. **delete children** in dependency order, then the item itself.

        Deleting something already deleted is a :class:`PrNotFoundError` from the
        lock, which is the same answer a client gets for any id that is not
        there - and the right one, because after this operation the id genuinely
        is not there.

        Raises:
            PrNotFoundError: No such content - including "somebody else deleted
                it a moment ago".
            PrPublishedContentError: The item has been published. Nobody may
                delete it; archiving is the operation for that.
            PrPermissionDeniedError: This actor may not delete this item.
                ``details['reason']`` distinguishes "not yours" from "too late"
                from "not at all".
        """
        content = await self._workflow.lock(content_id)
        await self._require_deletable(actor, content)

        receipt_stage = content.workflow_stage.value
        code, title = content.code, content.title

        # Written first, and inside the same transaction: if any delete below
        # fails, this row goes with it. An audit trail claiming a deletion that
        # was rolled back would be worse than no audit trail.
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_CONTENT_DELETED_PERMANENTLY,
            entity_type="pr_content_item",
            entity_id=content.id,
            after={
                # Plain text, no foreign key: this row outlives the content.
                "content_id": str(content.id),
                "content_code": code,
                "title": title,
                "stage_at_delete": receipt_stage,
                "reason": (reason or "").strip() or None,
                "deleted_at": utcnow().isoformat(),
                "deleted_by_user_id": str(actor.user_id) if actor.user_id else None,
            },
        )

        removed = await self._delete_aggregate(content)
        receipt = DeletionReceipt(
            content_id=content_id,
            content_code=code,
            title=title,
            stage_at_delete=receipt_stage,
            removed=removed,
        )
        logger.info(
            "pr_content_deleted_permanently",
            extra={
                "pr_content_id": str(content_id),
                "content_code": code,
                "stage_at_delete": receipt_stage,
                "rows_removed": receipt.total_rows,
            },
        )
        return receipt

    async def may_delete(self, actor: Actor, content: PrContentItem) -> bool:
        """The same decision as :meth:`delete_content`, as a boolean.

        For the action list, and reached by catching the refusal rather than by
        a second implementation - the pattern
        :meth:`PrCapabilityService.allows` established here. A screen offering a
        permanent delete the write would refuse is how somebody learns the panel
        lies, and this one is not an operation to learn that on.
        """
        try:
            await self._require_deletable(actor, content)
        except (PrPermissionDeniedError, PrPublishedContentError, PrContentHasRecordedWorkError):
            return False
        return True

    # --- The rule ---------------------------------------------------------
    async def _require_deletable(self, actor: Actor, content: PrContentItem) -> None:
        """Evaluate the domain rule against this actor and this row.

        The facts are gathered here - two capability lookups, two reads of the
        row, and at most one query - and the decision itself is
        :func:`~meobot.domain.pr.lifecycle.may_hard_delete`, which is pure and
        has its own tests. The responsibility query is skipped for a manager,
        because the answer cannot change theirs.

        The published refusal is raised as its own error because it is the one
        that is not about the actor: everybody gets it, and the useful next step
        is archiving rather than finding somebody with more rights.
        """
        may_delete = await self._capabilities.allows(actor, PrCapability.PR_CONTENT_DELETE)
        manages = await self._capabilities.allows(actor, PrCapability.PR_CONTENT_CANCEL)
        published = is_published_onward(content.workflow_stage)
        reached_production = has_reached_production(
            workflow_stage=content.workflow_stage,
            production_started_at=content.production_started_at,
        )
        responsible = (
            False if manages or actor.user_id is None else await self._responsible(actor, content)
        )
        if may_hard_delete(
            may_delete=may_delete,
            manages_content=manages,
            responsible=responsible,
            reached_production=reached_production,
            published_onward=published,
        ):
            # The same boundary, asked of the data. A publication row means the
            # piece went out, whatever the stage column currently says, and the
            # metrics hanging off it are what a report is made of. Belt and
            # braces on purpose: this is the one mistake in this service that
            # could not be undone, and the cost is one indexed ``EXISTS``.
            if await self._has_publications(content.id):
                raise self._published(content, reason="has_publications")
            # M3. The same shape as the line above, for the same reason: work
            # projected from this content is a **ledger** record with meaning of
            # its own, and somebody's KPI may already be counted against it.
            # Destroying it as a side effect of tidying a draft would change a
            # reported figure silently, in an operation whose audit row does not
            # even mention work.
            #
            # Refused rather than reversed here, because reversal is a decision:
            # the operator undoes the source milestone, the projector takes the
            # work back out **with its own audit trail**, and the delete then
            # goes through. That keeps the KPI change explicit and attributable
            # instead of hiding it inside a deletion.
            if await self._has_source_work(content.id):
                # Its own class, and a ``WorkflowStateError`` rather than a
                # validation error: nothing about the request is malformed, the
                # piece is simply in a state the rule protects. ``may_delete``
                # reads it as "no", so the action list never offers a delete
                # the write would refuse - which is what turned this refusal
                # into a broken *next actions* panel before.
                raise PrContentHasRecordedWorkError(
                    "Content that has produced recorded work cannot be permanently deleted",
                    details={
                        "content_id": str(content.id),
                        "content_code": content.code,
                        "reason": "has_recorded_work",
                        "workflow_stage": content.workflow_stage.value,
                    },
                )
            return

        if may_delete and published:
            raise self._published(content, reason="published")
        raise PrPermissionDeniedError(
            "This content may not be permanently deleted by this actor",
            details={
                "content_id": str(content.id),
                "content_code": content.code,
                "reason": _refusal_reason(
                    may_delete=may_delete,
                    responsible=responsible,
                    reached_production=reached_production,
                ),
                "workflow_stage": content.workflow_stage.value,
            },
        )

    async def _has_source_work(self, content_id: uuid.UUID) -> bool:
        """Has M3 projected any work out of this content?

        Matched on ``source_key``, because that is the only link there is: a
        projected work item carries ``content:{uuid}:{MILESTONE}`` as text and
        has **no** foreign key to the content. That is deliberate on M3's side -
        the ledger outlives the workflow that fed it - and it is exactly why this
        check has to exist here. No ``RESTRICT`` would fire; the rows would
        simply be left pointing at an id that is gone.

        **Every** projected item counts, including one already reversed to
        ``EXCLUDED``. A reversal says the work stopped counting, not that it
        never happened, and the history of a piece that once counted towards a
        month is what a correction is argued from.
        """
        pattern = f"content:{content_id}:%"
        found = await self._session.execute(
            select(
                exists().where(PrWorkItem.source_key.like(pattern))
                # The period-container patch: a milestone now lands as a
                # **result** in the writer's monthly stream, keyed the same way
                # and linked to the content by the same text. Every result
                # counts, excluded ones included, for the reason above.
                | exists().where(PrWorkResult.source_key.like(pattern))
            )
        )
        return bool(found.scalar())

    async def _has_publications(self, content_id: uuid.UUID) -> bool:
        """Has anything of this content ever been published?

        **Every** publication row counts, including one Step 1F.2.3f.1 marked
        ``REVERSED``. This is deliberately *not*
        :func:`~meobot.domain.pr.reporting.is_active_publication`, and the
        difference is the whole point: a reversal says the *record* was wrong,
        and a piece that has been through the act of being published has a
        history that must survive. Counting only the active ones would make
        "hoàn tác đăng bài" a way to earn the right to destroy the evidence,
        which is the one thing this floor exists to prevent.
        """
        found = await self._session.execute(
            select(exists().where(PrPublication.content_id == content_id))
        )
        return bool(found.scalar())

    @staticmethod
    def _published(content: PrContentItem, *, reason: str) -> PrPublishedContentError:
        return PrPublishedContentError(
            "Published content cannot be permanently deleted",
            details={
                "content_id": str(content.id),
                "content_code": content.code,
                "workflow_stage": content.workflow_stage.value,
                "reason": reason,
            },
        )

    async def _responsible(self, actor: Actor, content: PrContentItem) -> bool:
        """Is this actor the owner, or on an unfinished task of this item?

        Runs :func:`~meobot.application.pr_content_query.responsible_for` - the
        *same* predicate behind the ``MY_CONTENT`` scope and the "Người phụ
        trách" filter - against this one row. Reusing the expression rather than
        re-writing the two clauses is what keeps "what a member may delete" and
        "what the panel calls theirs" from drifting into two different answers.
        """
        if actor.user_id is None:
            return False
        found = await self._session.execute(
            select(PrContentItem.id).where(
                PrContentItem.id == content.id, responsible_for(actor.user_id)
            )
        )
        return found.scalars().first() is not None

    # --- The aggregate ----------------------------------------------------
    async def _delete_aggregate(self, content: PrContentItem) -> dict[str, int]:
        """Delete every content-owned row, children first, then the item.

        Each step is a single ``DELETE ... WHERE`` - by ``content_id`` where the
        table has one, and by a subquery over the parent's ids where it does not.
        No row is loaded into Python: the aggregate of a long-running piece is
        hundreds of rows across a dozen tables, and fetching them to call
        ``session.delete`` on each would be the N+1 shape this module avoids
        everywhere else.

        The locked instance is detached afterwards, so a later ``session.get``
        for that id asks the database - and gets ``None`` - instead of returning
        the cached object for a row that no longer exists, and so a later flush
        cannot emit an ``UPDATE`` against it. Deliberately ``expunge`` on the one
        object rather than ``expire_all``: expiring the whole identity map would
        make every *unrelated* instance the caller is holding reload on next
        access, which turns an ordinary attribute read in synchronous code into
        an IO attempt and a ``MissingGreenlet``.
        """
        content_id = content.id
        versions = select(PrContentVersion.id).where(PrContentVersion.content_id == content_id)
        tasks = select(PrTask.id).where(PrTask.content_id == content_id)
        runs = select(PrAiReviewRun.id).where(PrAiReviewRun.content_id == content_id)

        # Not a delete: an issue belongs to a reporting period and merely
        # *mentions* the content, and the column is nullable for exactly that
        # reason. Detaching keeps the report line and removes the dangling
        # reference; deleting it would take a paragraph out of somebody's weekly
        # write-up as a side effect of tidying a draft.
        detached = await self._session.execute(
            update(PrIssue)
            .where(PrIssue.content_id == content_id)
            .values(content_id=None)
            .execution_options(synchronize_session=False)
        )

        removed: dict[str, int] = {}
        for model, condition in self._plan(content_id, versions=versions, tasks=tasks, runs=runs):
            result = await self._session.execute(delete(model).where(condition))
            count = int(result.rowcount or 0)  # type: ignore[attr-defined]
            if count:
                removed[model.__tablename__] = count

        await self._session.execute(delete(PrContentItem).where(PrContentItem.id == content_id))
        await self._session.flush()
        if content in self._session:
            self._session.expunge(content)
        if int(detached.rowcount or 0):  # type: ignore[attr-defined]
            removed["pr_issues (detached)"] = int(detached.rowcount)  # type: ignore[attr-defined]
        return removed

    @staticmethod
    def _plan(
        content_id: uuid.UUID,
        *,
        versions: Select[tuple[uuid.UUID]],
        tasks: Select[tuple[uuid.UUID]],
        runs: Select[tuple[uuid.UUID]],
    ) -> Sequence[tuple[type[Base], ColumnElement[bool]]]:
        """The ordered delete plan: one ``(model, predicate)`` per table.

        A list rather than a chain of ``await``s so the order is legible as an
        order, and so a test can read it. See the module docstring for where the
        order comes from; the two subtle pairs are runs-before-reviews (a run
        points at the review it produced) and approvals-before-submissions.

        ``pr_publications``, ``pr_post_metric_snapshots``, ``pr_issues`` and
        ``pr_actions`` are deliberately absent - see the module docstring. The
        first two are refused rather than deleted; the second two are detached.
        """
        return (
            # M3, and first: pure operational scaffolding. It records that a
            # projection was *asked for*, holds no business meaning at all, and
            # its ``RESTRICT`` foreign key would otherwise refuse the delete.
            #
            # Safe to remove without a second thought precisely because the work
            # it would have produced is refused two steps earlier - so this can
            # only ever be a queue row for content that projected nothing.
            (PrContentWorkProjection, PrContentWorkProjection.content_id == content_id),
            (PrContentTransitionEvent, PrContentTransitionEvent.content_id == content_id),
            (PrAiReviewRunPolicyPack, PrAiReviewRunPolicyPack.run_id.in_(runs)),
            (PrAiReviewRun, PrAiReviewRun.content_id == content_id),
            (PrAiReview, PrAiReview.content_id == content_id),
            (PrApprovalEvent, PrApprovalEvent.content_id == content_id),
            # Step 1F.2.3f. **Before** the submissions, because a derivative may
            # name the master it was cut from and ``RESTRICT`` would refuse the
            # submission's delete while that link still existed. The pair is the
            # same shape as runs-before-reviews above, and the ordering is the
            # whole reason this plan is a list rather than a set.
            #
            # Reachable at all only because a *published* derivative blocks the
            # delete two steps earlier: ``_has_publications`` refuses the whole
            # operation, so nothing here can orphan a publication.
            (PrContentDerivative, PrContentDerivative.content_id == content_id),
            (PrProductionSubmission, PrProductionSubmission.content_id == content_id),
            (PrTaskAssignment, PrTaskAssignment.task_id.in_(tasks)),
            (PrTask, PrTask.content_id == content_id),
            (PrContentTarget, PrContentTarget.content_id == content_id),
            # Step 1F.2.3e. A leaf: it points only at the content item and at
            # a user, and nothing points at it. Placed beside the targets
            # because it is the same kind of thing - a row that describes the
            # item rather than a record of something that happened to it.
            #
            # Review material goes with the content it supports. Keeping a
            # brief after the piece it briefed is gone would leave a row
            # nothing can reach, pointing at a client document, with no
            # remaining record of what it was for.
            (PrContentResource, PrContentResource.content_id == content_id),
            # Step 1F.2.3f. A leaf beside the resources, and the same argument:
            # a landing-page link for a piece of content that no longer exists is
            # a row nothing can reach, describing a campaign nobody can look up.
            (PrContentDestination, PrContentDestination.content_id == content_id),
            (PrContentVersion, PrContentVersion.id.in_(versions)),
            # Step 1F.2.3g. Last, beside the versions: a conversation about a
            # piece of work belongs with the work, and keeping it afterwards
            # would leave rows nothing can reach discussing a draft nobody can
            # look up.
            #
            # **One statement, roots and replies together**, even though the
            # table references itself with ``RESTRICT``. That is safe for the
            # same reason ``pr_content_transition_events`` above is: PostgreSQL
            # applies a ``RESTRICT`` check against the rows still standing at
            # the *end* of the statement, so a ``DELETE`` that removes a root
            # and its answers in one pass leaves nothing dangling and is not
            # refused. Only a statement that removed a root and left a reply
            # behind would be, which this predicate cannot do - it matches by
            # ``content_id``, and a reply's content is its root's.
            (PrContentComment, PrContentComment.content_id == content_id),
        )


def _refusal_reason(*, may_delete: bool, responsible: bool, reached_production: bool) -> str:
    """Which half of the rule refused, as a code a client can word.

    Ordered by what is most useful to hear: "you may not delete anything" first,
    then "this is not yours", then the one that surprises people - *"this has
    been produced"*, which stays true even after an internal reviewer sends the
    cut back to production.
    """
    if not may_delete:
        return "missing_capability"
    if not responsible:
        return "not_responsible"
    if reached_production:
        return "already_produced"
    return "not_permitted"


__all__: list[str] = ["DeletionReceipt", "PrContentLifecycleService"]
