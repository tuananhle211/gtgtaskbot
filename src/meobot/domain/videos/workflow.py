"""Video production and publishing workflow.

Entry condition: the parent script must already be
``ScriptStatus.APPROVED_FOR_PRODUCTION``. Reaching
``VideoStatus.APPROVED_FOR_PUBLISH`` is a *separate* human decision - an
approved script never auto-promotes into publishable content.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType

from meobot.core.errors import WorkflowStateError
from meobot.domain.scripts.workflow import ScriptStatus


class VideoStatus(StrEnum):
    """States a video asset moves through, from filming to published."""

    NOT_STARTED = "not_started"
    FILMING = "filming"
    EDITING = "editing"
    VIDEO_SUBMITTED = "video_submitted"
    WAITING_FOR_VIDEO_APPROVAL = "waiting_for_video_approval"
    VIDEO_REVISION_REQUIRED = "video_revision_required"
    APPROVED_FOR_PUBLISH = "approved_for_publish"
    SCHEDULED = "scheduled"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    PUBLISH_FAILED = "publish_failed"


VIDEO_TRANSITIONS: Mapping[VideoStatus, frozenset[VideoStatus]] = MappingProxyType(
    {
        VideoStatus.NOT_STARTED: frozenset({VideoStatus.FILMING}),
        VideoStatus.FILMING: frozenset({VideoStatus.EDITING}),
        VideoStatus.EDITING: frozenset({VideoStatus.VIDEO_SUBMITTED}),
        VideoStatus.VIDEO_SUBMITTED: frozenset({VideoStatus.WAITING_FOR_VIDEO_APPROVAL}),
        VideoStatus.WAITING_FOR_VIDEO_APPROVAL: frozenset(
            {VideoStatus.APPROVED_FOR_PUBLISH, VideoStatus.VIDEO_REVISION_REQUIRED}
        ),
        VideoStatus.VIDEO_REVISION_REQUIRED: frozenset({VideoStatus.EDITING, VideoStatus.FILMING}),
        VideoStatus.APPROVED_FOR_PUBLISH: frozenset(
            {VideoStatus.SCHEDULED, VideoStatus.PUBLISHING}
        ),
        VideoStatus.SCHEDULED: frozenset(
            {VideoStatus.PUBLISHING, VideoStatus.APPROVED_FOR_PUBLISH}
        ),
        VideoStatus.PUBLISHING: frozenset({VideoStatus.PUBLISHED, VideoStatus.PUBLISH_FAILED}),
        VideoStatus.PUBLISHED: frozenset(),
        VideoStatus.PUBLISH_FAILED: frozenset({VideoStatus.SCHEDULED, VideoStatus.PUBLISHING}),
    }
)

#: Statuses from which publishing to a social platform is allowed at all.
PUBLISHABLE_STATUSES: frozenset[VideoStatus] = frozenset(
    {VideoStatus.APPROVED_FOR_PUBLISH, VideoStatus.SCHEDULED, VideoStatus.PUBLISHING}
)


def can_transition_video(current: VideoStatus, target: VideoStatus) -> bool:
    """True when ``current -> target`` is a legal video transition."""
    return target in VIDEO_TRANSITIONS[current]


def assert_video_transition(current: VideoStatus, target: VideoStatus) -> None:
    """Raise :class:`WorkflowStateError` when the transition is illegal."""
    if not can_transition_video(current, target):
        allowed = sorted(state.value for state in VIDEO_TRANSITIONS[current])
        raise WorkflowStateError(
            f"Cannot move video from {current.value!r} to {target.value!r}",
            details={"current": current.value, "target": target.value, "allowed": allowed},
        )


def assert_production_allowed(script_status: ScriptStatus) -> None:
    """Guard the script -> video boundary.

    Filming may only start once the script workflow reached
    ``approved_for_production``. This is deliberately the *only* coupling point
    between the two workflows.
    """
    allowed = {ScriptStatus.APPROVED_FOR_PRODUCTION, ScriptStatus.IN_PRODUCTION}
    if script_status not in allowed:
        raise WorkflowStateError(
            "Video production requires a script approved for production",
            details={"script_status": script_status.value},
        )
