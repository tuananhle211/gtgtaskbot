"""Workflow transition rules and the separation of the two approvals."""

from __future__ import annotations

import itertools

import pytest

from meobot.core.errors import WorkflowStateError
from meobot.domain.scripts.workflow import (
    SCRIPT_TRANSITIONS,
    ScriptStatus,
    assert_script_transition,
    can_transition_script,
)
from meobot.domain.videos.workflow import (
    PUBLISHABLE_STATUSES,
    VIDEO_TRANSITIONS,
    VideoStatus,
    assert_production_allowed,
    assert_video_transition,
    can_transition_video,
)


def test_script_happy_path() -> None:
    path = [
        ScriptStatus.DRAFT,
        ScriptStatus.SUBMITTED_FOR_REVIEW,
        ScriptStatus.AI_REVIEWED,
        ScriptStatus.WAITING_FOR_SCRIPT_APPROVAL,
        ScriptStatus.APPROVED_FOR_PRODUCTION,
        ScriptStatus.IN_PRODUCTION,
    ]
    for current, target in itertools.pairwise(path):
        assert can_transition_script(current, target), f"{current} -> {target}"


def test_script_revision_loop() -> None:
    assert can_transition_script(
        ScriptStatus.WAITING_FOR_SCRIPT_APPROVAL, ScriptStatus.REVISION_REQUIRED
    )
    assert can_transition_script(ScriptStatus.REVISION_REQUIRED, ScriptStatus.DRAFT)


def test_script_cannot_skip_review() -> None:
    with pytest.raises(WorkflowStateError):
        assert_script_transition(ScriptStatus.DRAFT, ScriptStatus.APPROVED_FOR_PRODUCTION)


def test_video_happy_path() -> None:
    path = [
        VideoStatus.NOT_STARTED,
        VideoStatus.FILMING,
        VideoStatus.EDITING,
        VideoStatus.VIDEO_SUBMITTED,
        VideoStatus.WAITING_FOR_VIDEO_APPROVAL,
        VideoStatus.APPROVED_FOR_PUBLISH,
        VideoStatus.SCHEDULED,
        VideoStatus.PUBLISHING,
        VideoStatus.PUBLISHED,
    ]
    for current, target in itertools.pairwise(path):
        assert can_transition_video(current, target), f"{current} -> {target}"


def test_publish_failure_can_be_retried() -> None:
    assert can_transition_video(VideoStatus.PUBLISHING, VideoStatus.PUBLISH_FAILED)
    assert can_transition_video(VideoStatus.PUBLISH_FAILED, VideoStatus.PUBLISHING)


def test_published_is_terminal() -> None:
    with pytest.raises(WorkflowStateError):
        assert_video_transition(VideoStatus.PUBLISHED, VideoStatus.EDITING)


def test_approved_script_does_not_imply_publishable_video() -> None:
    """ADR-002 in code: the two approvals are different decisions."""
    assert ScriptStatus.APPROVED_FOR_PRODUCTION.value != VideoStatus.APPROVED_FOR_PUBLISH.value
    assert VideoStatus.NOT_STARTED not in PUBLISHABLE_STATUSES
    assert VideoStatus.EDITING not in PUBLISHABLE_STATUSES
    assert VideoStatus.APPROVED_FOR_PUBLISH in PUBLISHABLE_STATUSES


def test_production_requires_an_approved_script() -> None:
    assert_production_allowed(ScriptStatus.APPROVED_FOR_PRODUCTION)
    with pytest.raises(WorkflowStateError):
        assert_production_allowed(ScriptStatus.WAITING_FOR_SCRIPT_APPROVAL)


def test_video_approval_cannot_be_skipped() -> None:
    with pytest.raises(WorkflowStateError):
        assert_video_transition(VideoStatus.EDITING, VideoStatus.APPROVED_FOR_PUBLISH)


def test_every_state_has_a_transition_entry() -> None:
    """A state missing from the table would raise KeyError the first time it is used."""
    assert set(SCRIPT_TRANSITIONS) == set(ScriptStatus)
    assert set(VIDEO_TRANSITIONS) == set(VideoStatus)


def test_no_transition_points_at_an_undefined_state() -> None:
    for targets in SCRIPT_TRANSITIONS.values():
        assert targets <= set(ScriptStatus)
    for targets in VIDEO_TRANSITIONS.values():
        assert targets <= set(VideoStatus)
