"""Script review workflow.

``approved_for_production`` is the terminal *approval* of this workflow. It
authorises filming and nothing else. Permission to publish is granted only by
the separate video workflow (see ``meobot.domain.videos.workflow``) - see ADR-002
in the README.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType

from meobot.core.errors import WorkflowStateError


class ScriptStatus(StrEnum):
    """States a script moves through, from draft to production hand-off.

    Milestone 2 added the three states a *sheet-imported* script needs
    (``imported``, ``reviewing``, ``archived``); the milestone-1 states keep
    their exact meaning so existing history stays readable.
    """

    DRAFT = "draft"
    IMPORTED = "imported"
    SUBMITTED_FOR_REVIEW = "submitted_for_review"
    REVIEWING = "reviewing"
    AI_REVIEWED = "ai_reviewed"
    WAITING_FOR_SCRIPT_APPROVAL = "waiting_for_script_approval"
    REVISION_REQUIRED = "revision_required"
    APPROVED_FOR_PRODUCTION = "approved_for_production"
    IN_PRODUCTION = "in_production"
    ARCHIVED = "archived"


#: Statuses a human is expected to act on. Used by ``/pending_scripts``.
PENDING_REVIEW_STATUSES: frozenset[ScriptStatus] = frozenset(
    {
        ScriptStatus.IMPORTED,
        ScriptStatus.SUBMITTED_FOR_REVIEW,
        ScriptStatus.AI_REVIEWED,
        ScriptStatus.WAITING_FOR_SCRIPT_APPROVAL,
        ScriptStatus.REVISION_REQUIRED,
    }
)

#: Legal transitions. Anything not listed here is rejected.
SCRIPT_TRANSITIONS: Mapping[ScriptStatus, frozenset[ScriptStatus]] = MappingProxyType(
    {
        ScriptStatus.DRAFT: frozenset({ScriptStatus.SUBMITTED_FOR_REVIEW, ScriptStatus.ARCHIVED}),
        ScriptStatus.IMPORTED: frozenset(
            {ScriptStatus.SUBMITTED_FOR_REVIEW, ScriptStatus.ARCHIVED}
        ),
        ScriptStatus.SUBMITTED_FOR_REVIEW: frozenset(
            {
                ScriptStatus.REVIEWING,
                ScriptStatus.AI_REVIEWED,
                ScriptStatus.REVISION_REQUIRED,
                ScriptStatus.ARCHIVED,
            }
        ),
        ScriptStatus.REVIEWING: frozenset(
            {
                ScriptStatus.AI_REVIEWED,
                # A failed review returns the script to the queue.
                ScriptStatus.SUBMITTED_FOR_REVIEW,
                ScriptStatus.REVISION_REQUIRED,
                ScriptStatus.ARCHIVED,
            }
        ),
        ScriptStatus.AI_REVIEWED: frozenset(
            {
                ScriptStatus.WAITING_FOR_SCRIPT_APPROVAL,
                ScriptStatus.REVISION_REQUIRED,
                # Re-review of the same version.
                ScriptStatus.SUBMITTED_FOR_REVIEW,
                ScriptStatus.ARCHIVED,
            }
        ),
        ScriptStatus.WAITING_FOR_SCRIPT_APPROVAL: frozenset(
            {
                ScriptStatus.APPROVED_FOR_PRODUCTION,
                ScriptStatus.REVISION_REQUIRED,
                ScriptStatus.SUBMITTED_FOR_REVIEW,
                ScriptStatus.ARCHIVED,
            }
        ),
        ScriptStatus.REVISION_REQUIRED: frozenset(
            {ScriptStatus.DRAFT, ScriptStatus.SUBMITTED_FOR_REVIEW, ScriptStatus.ARCHIVED}
        ),
        # A sheet edit after approval invalidates the approval: the new version
        # must be reviewed and approved again (the old approval stays historical).
        ScriptStatus.APPROVED_FOR_PRODUCTION: frozenset(
            {
                ScriptStatus.IN_PRODUCTION,
                ScriptStatus.SUBMITTED_FOR_REVIEW,
                ScriptStatus.ARCHIVED,
            }
        ),
        ScriptStatus.IN_PRODUCTION: frozenset({ScriptStatus.ARCHIVED}),
        ScriptStatus.ARCHIVED: frozenset(),
    }
)


def can_transition_script(current: ScriptStatus, target: ScriptStatus) -> bool:
    """True when ``current -> target`` is a legal script transition."""
    return target in SCRIPT_TRANSITIONS[current]


def assert_script_transition(current: ScriptStatus, target: ScriptStatus) -> None:
    """Raise :class:`WorkflowStateError` when the transition is illegal."""
    if not can_transition_script(current, target):
        allowed = sorted(state.value for state in SCRIPT_TRANSITIONS[current])
        raise WorkflowStateError(
            f"Cannot move script from {current.value!r} to {target.value!r}",
            details={"current": current.value, "target": target.value, "allowed": allowed},
        )
