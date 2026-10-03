"""TikTok Business metrics protocol and fake."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Protocol, runtime_checkable

from meobot.core.errors import IntegrationNotConfiguredError


@dataclass(frozen=True, slots=True)
class TikTokVideoMetrics:
    """A point-in-time snapshot of one TikTok video."""

    video_id: str
    account_id: str
    captured_on: date
    views: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    average_watch_time_seconds: float = 0.0


@runtime_checkable
class TikTokMetricsClient(Protocol):
    """Reads TikTok Business analytics."""

    async def fetch_video_metrics(
        self, account_id: str, video_ids: list[str]
    ) -> list[TikTokVideoMetrics]:
        """Return one snapshot per requested video."""
        ...

    async def fetch_account_video_ids(
        self, account_id: str, *, since: date, until: date
    ) -> list[str]:
        """Return video ids posted in the window."""
        ...


@dataclass
class FakeTikTokMetricsClient:
    """Serves canned TikTok metrics for tests."""

    snapshots: dict[str, TikTokVideoMetrics] = field(default_factory=dict)
    account_videos: dict[str, list[str]] = field(default_factory=dict)

    async def fetch_video_metrics(
        self, account_id: str, video_ids: list[str]
    ) -> list[TikTokVideoMetrics]:
        return [self.snapshots[vid] for vid in video_ids if vid in self.snapshots]

    async def fetch_account_video_ids(
        self, account_id: str, *, since: date, until: date
    ) -> list[str]:
        return list(self.account_videos.get(account_id, []))


class NotConfiguredTikTokMetricsClient:
    """Fails loudly until TikTok credentials exist.

    TODO(milestone-4): OAuth flow via the FastAPI callback route, refresh token
    stored encrypted (never logged, never in ``.env``).
    """

    provider = "tiktok"

    async def fetch_video_metrics(
        self, account_id: str, video_ids: list[str]
    ) -> list[TikTokVideoMetrics]:
        raise IntegrationNotConfiguredError(
            "TikTok metrics is not configured", provider=self.provider
        )

    async def fetch_account_video_ids(
        self, account_id: str, *, since: date, until: date
    ) -> list[str]:
        raise IntegrationNotConfiguredError(
            "TikTok metrics is not configured", provider=self.provider
        )
