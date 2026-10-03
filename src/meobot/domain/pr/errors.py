"""PR-specific failures, each a subclass of the one MeoBot error it *is*.

Two things have to be true at once, and a flat list of new exception types
would satisfy only the second:

* **Existing boundaries keep working unchanged.** Every transport already maps
  :class:`~meobot.core.errors.NotFoundError`,
  :class:`~meobot.core.errors.ConflictError`,
  :class:`~meobot.core.errors.WorkflowStateError` and the rest to a response.
  Each error here inherits from whichever of those it genuinely is, so a
  handler that has never heard of the PR module still does the right thing
  with a stale-version failure.
* **A caller that cares can tell them apart.** "The version you edited is no
  longer current" and "two people are assigned to this channel over the same
  dates" are both conflicts, and a client that wants to offer different next
  steps needs to distinguish them without matching on message text. That is
  what the distinct classes and their :attr:`code` strings are for.

**Messages are English and structural, not Vietnamese and conversational.**
This is a deliberate departure from
:mod:`meobot.application.hr_request_service`, which embeds the exact sentence a
person reads. PR services are shared by the Telegram bot, a future web admin UI
and CLI/admin scripts (see ``docs/pr/STEP_1C_APPLICATION_SERVICES.md``), so the
wording a human sees belongs to whichever client is talking to them. What
crosses this boundary is a code and a ``details`` mapping the client can
render.
"""

from __future__ import annotations

from meobot.core.errors import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ValidationError,
    WorkflowStateError,
)


class PrNotFoundError(NotFoundError):
    """A referenced PR entity does not exist."""

    code = "pr_not_found"


class PrValidationError(ValidationError):
    """Input violates a PR domain rule that is not about state or versions."""

    code = "pr_validation_error"


class PrWorkflowTransitionError(WorkflowStateError):
    """The requested stage change is not legal from the current stage."""

    code = "pr_invalid_transition"


class PrStaleVersionError(ConflictError):
    """The caller edited a draft that is no longer the latest one.

    Raised by :meth:`~meobot.application.pr_content_service.PrContentService.revise_content`
    when ``expected_version`` does not match the newest persisted version. The
    caller is holding a screen drawn from a draft somebody has since replaced.
    """

    code = "pr_stale_version"


class PrReviewVersionMismatchError(ConflictError):
    """A review was submitted against a version that is not the current one.

    The rule this protects is the whole reason ``reviewed_version`` and
    ``version_reviewed`` exist: a verdict belongs to the draft it was given,
    and may never be applied to a later one.
    """

    code = "pr_review_version_mismatch"


class PrAiReviewRequiredError(WorkflowStateError):
    """The content cannot move on because the current version has no AI verdict.

    Distinct from :class:`PrWorkflowTransitionError`: the transition itself is
    legal, and the answer to "what do I do about it" is "run the review", not
    "you asked for the wrong thing".
    """

    code = "pr_ai_review_required"


class PrApprovalStageMismatchError(WorkflowStateError):
    """The decision was filed at a gate the content is not standing at."""

    code = "pr_approval_stage_mismatch"


class PrPermissionDeniedError(AuthorizationError):
    """The actor may not perform this PR action."""

    code = "pr_forbidden"


class PrAssignmentOverlapError(ConflictError):
    """A channel assignment would overlap another for the same person and role."""

    code = "pr_assignment_overlap"


class PrImmutableFieldError(ValidationError):
    """An update tried to change a field that is write-once."""

    code = "pr_immutable_field"


class PrConflictError(ConflictError):
    """The operation duplicates or contradicts something already recorded."""

    code = "pr_conflict"


class PrProductionClaimConflictError(ConflictError):
    """Somebody else took this production between the screen and the press.

    Step 1F.2.3, and its own class rather than a plain
    :class:`PrConflictError` because the sentence a client should write is
    specific and reassuring - *"đã có người nhận"* - while a generic conflict
    reads as "something went wrong". ``details`` carries the producer already on
    the row, so a screen can name them instead of asking for a reload.
    """

    code = "pr_production_claimed"


class PrUndoNotAvailableError(WorkflowStateError):
    """There is nothing to take back, or it is too late to take it back.

    Step 1F.2.3b. One class for every way an undo can be refused, with
    ``details['reason']`` naming which - ``nothing_to_undo``, ``superseded``,
    ``not_reversible``, ``published``, ``production_handed_off``,
    ``production_started``, ``production_submitted``, ``already_published``,
    ``new_version_written``, ``new_submission``.

    One class rather than ten because a client does the same thing with all of
    them: it stops offering the button and says why. The reason is a field
    rather than a class for the same reason it is a field on the delete
    refusals - the set will grow, and a new member of it should not be a new
    import for every caller.
    """

    code = "pr_undo_not_available"


class PrBulkApprovalStaleError(ConflictError):
    """A bulk approval was refused because the batch no longer describes reality.

    Step 1F.2.8, and the load-bearing half of the all-or-nothing promise: an item
    in the batch is no longer standing at the gate the batch was built for -
    somebody else approved it, sent it back, or cancelled it between the checkbox
    and the button. **Nothing in the batch was approved**, and ``details`` says
    which items caused it so the panel can name them rather than asking for a
    reload.

    ``details``: ``reason`` (``moved``, ``not_at_a_gate``, ``missing``),
    ``approved`` (always ``0``), ``gate``, and ``affected`` - a list of
    ``{content_id, code, current_stage, reason}``.
    """

    code = "pr_bulk_approval_stale"


class PrBulkApprovalUnauthorizedError(AuthorizationError):
    """A bulk approval was refused because the actor may not decide one of its items.

    Step 1F.2.8. Not a different *rule* from the single-item refusal - the check
    is the same :meth:`PrCapabilityService.require_approval`, item by item, under
    the same lock - but a different *sentence*: one item outside the actor's
    scope means none of the batch is approved, and a client saying "you may not
    approve this" about a screen showing twelve cards would be describing the
    wrong thing.

    ``details``: ``reason`` (``out_of_grant_scope`` or ``missing_grant``),
    ``approved`` (always ``0``), ``gate``, and ``affected``.
    """

    code = "pr_bulk_approval_forbidden"


class PrBulkArchiveStaleError(ConflictError):
    """A period archive was refused because the batch no longer describes the month.

    Step 1F.2.3f.6, and the same promise as
    :class:`PrBulkApprovalStaleError`: an item in the batch is no longer a
    ``PUBLISHED`` piece whose canonical publication instant is in the requested
    month - somebody archived it already, reversed its publication, corrected
    its date into another month, or it never existed. **Nothing in the batch was
    archived.** A retry after a successful run lands here too, with every item
    reported as ``already_archived``, which is the honest answer rather than a
    silent second success.

    ``details``: ``reason`` (``already_archived``, ``moved``, ``not_published``,
    ``outside_period``, ``missing``), ``archived`` (always ``0``), ``period``,
    and ``affected`` - a list of ``{content_id, code, current_stage, reason}``.
    """

    code = "pr_bulk_archive_stale"


class PrPublishedContentError(WorkflowStateError):
    """Published work cannot be permanently deleted, by anybody.

    Step 1F.2.3a, and its own class because it is the one refusal in the delete
    path that is **not** about who is asking. A member is told "not yours" and a
    lead can still act; this is told to everyone, including an ``OWNER``, and the
    only thing that would change the answer is the content not having gone out.
    A client should say so - *"hãy lưu trữ nội dung thay thế"* - rather than
    offering to find somebody with more rights.

    ``details`` carries the stage, so the sentence can name it.
    """

    code = "pr_published_content"


class PrContentHasRecordedWorkError(WorkflowStateError):
    """Content that produced recorded work cannot be permanently deleted.

    The second refusal in the delete path that is **not** about who is asking,
    and its own class for the same reason :class:`PrPublishedContentError` is:
    the ledger holds a result or a work item projected out of this piece, and
    somebody's month may already be counted against it. Nothing about the actor
    changes that, so a client must not offer to retry - the useful next step is
    to leave the piece alone, or to have the source milestone reversed so the
    projector takes the work back out with its own audit trail.

    A deterministic business rule: the delete wrote nothing before raising, the
    stage, the production handoff and the work are exactly as they were, and
    the same request will be refused for the same reason until the facts change.
    ``details`` carries the content code and the stage so the sentence can name
    them.
    """

    code = "pr_content_delete_blocked_recorded_work"


class PrWorkPeriodNotOpenError(WorkflowStateError):
    """A quota recomputation was asked for on a period that is not ``OPEN``. M2.

    Its own class rather than a generic conflict, because the answer to it is
    specific and the caller needs to be able to say it: *the numbers for this
    month were agreed, so eligibility is not recalculated any more.* Correcting
    a ``CLOSED`` or ``LOCKED`` period is a deliberate administrative act with
    its own trail, and it belongs to a later milestone.

    **Refused rather than silently ignored.** A no-op would hide an operational
    mistake - somebody reconciling the wrong month and believing it worked - and
    there is deliberately no ``force`` flag to get past it.
    """

    code = "pr_work_period_not_open"


class PrWorkPlanStateError(WorkflowStateError):
    """A KPI plan was asked to do something its current status forbids. M2.

    Editing a quota on an approved plan, approving a draft twice, revising a
    version that is already superseded. ``details`` carries the plan's status
    and what was attempted, so a client can offer the real next step - which is
    nearly always *"create a revision"*.
    """

    code = "pr_work_plan_state"


#: Removed in Step 1F.2.2 along with the rule it named. ``PrReviewerSeparationError``
#: reported "one person tried to be both the Team Lead and the Head for one
#: draft", and that pairing is now legal when the person independently holds both
#: grants. Nothing raised it once the predicate went, and an exception class no
#: code can produce is a claim about behaviour the module no longer has - so it
#: is gone rather than deprecated. The gates themselves are untouched: both are
#: still mandatory and each still needs its own capability.
#:
#: What still refuses a Head approval, and with which error:
#:
#: * no ``TEAM_LEAD_REVIEW`` approval on file for this draft -> ``PrWorkflowTransitionError``
#: * the actor lacks ``PR_HEAD_REVIEW`` -> ``PrPermissionDeniedError``
#: * the content is not standing at the Head gate -> ``PrApprovalStageMismatchError``
#: * the draft moved on -> ``PrReviewVersionMismatchError``


__all__: list[str] = [
    "PrAiReviewRequiredError",
    "PrApprovalStageMismatchError",
    "PrAssignmentOverlapError",
    "PrBulkApprovalStaleError",
    "PrBulkApprovalUnauthorizedError",
    "PrBulkArchiveStaleError",
    "PrConflictError",
    "PrImmutableFieldError",
    "PrNotFoundError",
    "PrPermissionDeniedError",
    "PrProductionClaimConflictError",
    "PrPublishedContentError",
    "PrReviewVersionMismatchError",
    "PrStaleVersionError",
    "PrUndoNotAvailableError",
    "PrValidationError",
    "PrWorkPeriodNotOpenError",
    "PrWorkPlanStateError",
    "PrWorkflowTransitionError",
]
