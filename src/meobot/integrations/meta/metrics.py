"""Facebook Page/post metrics protocol and fake."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Protocol, runtime_checkable

from meobot.core.errors import IntegrationNotConfiguredError


@dataclass(frozen=True, slots=True)
class PostMetrics:
    """A point-in-time snapshot of one post's performance."""

    post_id: str
    page_id: str
    captured_on: date
    impressions: int = 0
    reach: int = 0
    reactions: int = 0
    comments: int = 0
    shares: int = 0
    video_views: int = 0


@runtime_checkable
class FacebookMetricsClient(Protocol):
    """Pulls insights for Pages and posts."""

    async def fetch_post_metrics(self, page_id: str, post_ids: list[str]) -> list[PostMetrics]:
        """Return one snapshot per requested post."""
        ...

    async def fetch_page_post_ids(self, page_id: str, *, since: date, until: date) -> list[str]:
        """Return post ids published in the window."""
        ...


@dataclass
class FakeFacebookMetricsClient:
    """Serves canned metrics; used by tests and the demo report task."""

    snapshots: dict[str, PostMetrics] = field(default_factory=dict)
    page_posts: dict[str, list[str]] = field(default_factory=dict)

    async def fetch_post_metrics(self, page_id: str, post_ids: list[str]) -> list[PostMetrics]:
        return [self.snapshots[pid] for pid in post_ids if pid in self.snapshots]

    async def fetch_page_post_ids(self, page_id: str, *, since: date, until: date) -> list[str]:
        return list(self.page_posts.get(page_id, []))


class NotConfiguredFacebookMetricsClient:
    """Fails loudly until Meta credentials exist.

    TODO(milestone-4): Graph API insights with per-Page token rotation.
    """

    provider = "meta"

    async def fetch_post_metrics(self, page_id: str, post_ids: list[str]) -> list[PostMetrics]:
        raise IntegrationNotConfiguredError(
            "Facebook metrics is not configured", provider=self.provider
        )

    async def fetch_page_post_ids(self, page_id: str, *, since: date, until: date) -> list[str]:
        raise IntegrationNotConfiguredError(
            "Facebook metrics is not configured", provider=self.provider
        )
