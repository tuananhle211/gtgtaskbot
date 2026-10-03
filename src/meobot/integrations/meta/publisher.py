"""Facebook publishing protocol and fake.

Publishing is the highest-consequence action MeoBot can take. Two guarantees
are baked into the interface itself:

1. Every request carries an ``idempotency_key`` - a retry can never double-post.
2. The caller must pass the video's workflow status, so a publisher
   implementation can refuse content that was not approved for publish.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

from meobot.core.errors import IntegrationNotConfiguredError, WorkflowStateError
from meobot.core.time import utcnow
from meobot.domain.videos.workflow import PUBLISHABLE_STATUSES, VideoStatus


@dataclass(frozen=True, slots=True)
class PublishRequest:
    """One publish attempt to one Page."""

    page_id: str
    video_id: str
    caption: str
    media_url: str
    video_status: VideoStatus
    idempotency_key: str
    scheduled_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class PublishResult:
    """Outcome of a publish attempt."""

    post_id: str
    permalink: str
    published_at: datetime
    was_duplicate: bool = False


@runtime_checkable
class FacebookPublisher(Protocol):
    """Publishes approved videos to a Facebook Page."""

    async def publish_video(self, request: PublishRequest) -> PublishResult:
        """Publish (or schedule) a video. Must be idempotent per key."""
        ...


def assert_publishable(status: VideoStatus) -> None:
    """Last-line defence before any real publish call.

    The policy engine already blocks unapproved content, but publishers
    re-check because this is the one action that cannot be undone quietly.
    """
    if status not in PUBLISHABLE_STATUSES:
        raise WorkflowStateError(
            "Refusing to publish a video that is not approved for publish",
            details={"video_status": status.value},
        )


@dataclass
class FakeFacebookPublisher:
    """In-memory publisher that records calls and enforces the same guards."""

    published: dict[str, PublishResult] = field(default_factory=dict)
    requests: list[PublishRequest] = field(default_factory=list)
    now: datetime | None = None

    async def publish_video(self, request: PublishRequest) -> PublishResult:
        assert_publishable(request.video_status)
        self.requests.append(request)
        existing = self.published.get(request.idempotency_key)
        if existing is not None:
            return PublishResult(
                post_id=existing.post_id,
                permalink=existing.permalink,
                published_at=existing.published_at,
                was_duplicate=True,
            )
        post_id = f"{request.page_id}_{len(self.published) + 1}"
        result = PublishResult(
            post_id=post_id,
            permalink=f"https://example.invalid/{post_id}",
            published_at=self.now or utcnow(),
        )
        self.published[request.idempotency_key] = result
        return result


class NotConfiguredFacebookPublisher:
    """Fails loudly until Meta credentials and Page tokens exist.

    TODO(milestone-4): implement with httpx against the Graph API, storing
    per-Page access tokens encrypted, never in ``.env``.
    """

    provider = "meta"

    async def publish_video(self, request: PublishRequest) -> PublishResult:
        raise IntegrationNotConfiguredError(
            "Facebook publishing is not configured", provider=self.provider
        )
