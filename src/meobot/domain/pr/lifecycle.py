"""When a content item may be destroyed, and what "has been produced" means.

Step 1F.2.3a. Two decisions, both pure, both deliberately out of the service
that executes them so they can be read as rules rather than reconstructed from a
sequence of ``await``s.

Delete means delete
-------------------

Step 1F.2.3 shipped "Xóa nội dung" as a soft delete - a hidden row, still
cancelled, with every child record intact. That is not what the team means by
the word, and a workspace that quietly keeps everything is a workspace nobody
trusts to be tidy. Deletion is now **permanent removal of the whole aggregate**:
the item, its drafts, its AI reviews, its approvals, its tasks and its production
files, in one transaction, gone.

Which makes the eligibility rule the only thing standing between a mis-click and
unrecoverable work, so it is written here, as one function, with one test file
against it.

Three questions, in this order
------------------------------

1. **May this actor delete at all?** ``PR_CONTENT_DELETE``, which every writer
   holds. Without it the answer is no whatever else is true.
2. **Has this been published?** ``PUBLISHED``, ``MEASURED`` and ``ARCHIVED`` are
   an absolute floor. Nobody crosses it - not a lead, not an ``OWNER``, not a
   direct API call, and there is deliberately no force flag to add one later.
   Published work is operational history: it went out, people saw it, and the
   numbers hanging off it are what the reports are made of. "We deleted the
   record of something the public already has" is not a state this module will
   produce.
3. **Whose work is it, and how far has it gone?** Management - anybody holding
   ``PR_CONTENT_CANCEL``, i.e. who may already end this piece of work - deletes
   anything below the floor. A member deletes only their own, and only while it
   is still only theirs: something they are responsible for that has never been
   produced.

The member half is stricter than the management half on purpose, and the
asymmetry is the point. A member removing a draft they opened by mistake is
housekeeping. The same action against a piece a producer has already cut destroys
a colleague's afternoon, and there is no undo - so their right stops permanently
at the moment the work leaves their hands. A lead may still delete it, because
somebody has to be able to, and because that decision has a name attached in the
audit trail.

Every input is a capability or a stored fact. There is no role string anywhere in
this module, no ``OWNER`` special case, and no bypass.

"Has ever reached production" is a stored fact
----------------------------------------------

The rule is *ever*, not *now*, and the two differ whenever an internal reviewer
sends a cut back or an operator corrects a stage by hand. Reading the current
stage alone would hand the delete button back to a member the moment their piece
returned to ``PRODUCTION``'s predecessors, which is precisely when the piece has
most work in it.

So :func:`has_reached_production` reads
``pr_content_items.production_started_at`` - a column stamped once, by
:meth:`~meobot.application.pr_workflow_service.PrContentWorkflowService.apply`,
the first time the item enters ``PRODUCTION``, and never cleared. It is the same
shape as ``archived_at``, which this module's neighbours have used since Step 1A:
a dated fact about a thing that happened, not a derived flag.

The current stage is consulted **as well**, and only as a floor: a row that was
already past ``PRODUCTION`` before revision 0020 existed has no stamp, and
answering "never produced" for a published piece because a column was added late
would be the worst possible reading. Either says yes; neither alone is trusted to
say no.
"""

from __future__ import annotations

from datetime import datetime

from meobot.domain.pr.models import PrWorkflowStage

_STAGE_ORDER: tuple[PrWorkflowStage, ...] = tuple(PrWorkflowStage)


def _from(first: PrWorkflowStage) -> frozenset[PrWorkflowStage]:
    """``first`` and everything the workflow can reach after it.

    Read off the canonical member order of
    :class:`~meobot.domain.pr.models.PrWorkflowStage` rather than listed twice.
    ``CANCELLED`` is excluded explicitly: it is last in the enum because it
    belongs to no position in the sequence, not because it is the end of it, and
    a cancelled idea has neither been produced nor published.
    """
    start = _STAGE_ORDER.index(first)
    return frozenset(
        stage
        for index, stage in enumerate(_STAGE_ORDER)
        if index >= start and stage is not PrWorkflowStage.CANCELLED
    )


#: ``PRODUCTION`` onwards. The floor for a **member's** delete right.
PRODUCTION_ONWARD_STAGES: frozenset[PrWorkflowStage] = _from(PrWorkflowStage.PRODUCTION)

#: ``PUBLISHED`` onwards. The floor for **everybody's**, with no override - see
#: the module docstring. ``READY_TO_PUBLISH`` is deliberately *below* it: content
#: that is ready has not gone out, and a piece scheduled by mistake must still be
#: removable.
PUBLISHED_ONWARD_STAGES: frozenset[PrWorkflowStage] = _from(PrWorkflowStage.PUBLISHED)


def has_reached_production(
    *, workflow_stage: PrWorkflowStage, production_started_at: datetime | None
) -> bool:
    """Whether this item has *ever* entered production.

    ``True`` if either the stamp is set or the item is standing at
    ``PRODUCTION`` or later. See the module docstring for why both are read and
    why neither is trusted alone to answer "no".
    """
    return production_started_at is not None or workflow_stage in PRODUCTION_ONWARD_STAGES


def is_published_onward(workflow_stage: PrWorkflowStage) -> bool:
    """Whether the item has gone out, and is therefore undeletable by anyone."""
    return workflow_stage in PUBLISHED_ONWARD_STAGES


def may_hard_delete(
    *,
    may_delete: bool,
    manages_content: bool,
    responsible: bool,
    reached_production: bool,
    published_onward: bool,
) -> bool:
    """The permanent-delete rule, as one expression over five facts.

    Args:
        may_delete: Holds ``PR_CONTENT_DELETE``. Necessary in every case - the
            action needs its capability like any other, and a reader with no
            write rights may not delete their own responsibility either.
        manages_content: Holds ``PR_CONTENT_CANCEL``, i.e. may already end this
            piece of work. This is what "management deletion" is; there is no
            other test for it, and in particular no role comparison.
        responsible: Is the owner, or holds an unfinished task assignment on it.
            The **same** relation ``MY_CONTENT`` and the "Người phụ trách" filter
            use - :func:`~meobot.application.pr_content_query.responsible_for` -
            so a member's delete right covers exactly what the panel already
            tells them is theirs.
        reached_production: :func:`has_reached_production`. Bars the **member**
            permanently, including after the work moves backwards.
        published_onward: :func:`is_published_onward`. Bars **everybody**, and is
            checked before anything else can grant.

    Returns:
        ``True`` when this delete is allowed. The caller raises; deciding and
        refusing are different jobs and only the second needs a message.
    """
    if not may_delete:
        return False
    if published_onward:
        # First and unconditional. Written above the management branch rather
        # than inside it so that no future capability can be added that skips
        # it: to grant a bypass here somebody would have to move this line.
        return False
    if manages_content:
        return True
    return responsible and not reached_production


__all__: list[str] = [
    "PRODUCTION_ONWARD_STAGES",
    "PUBLISHED_ONWARD_STAGES",
    "has_reached_production",
    "is_published_onward",
    "may_hard_delete",
]
