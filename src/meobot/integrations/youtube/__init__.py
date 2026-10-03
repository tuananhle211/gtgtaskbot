"""YouTube channel connector: OAuth, Data API, Analytics API.

Step 1F.2.4b. The only concrete platform connector MeoBot has. Everything here
is transport - building URLs, sending requests, reading JSON, mapping failures
onto a closed vocabulary - and nothing here knows what a PR channel is or when a
sync should run. That is
:mod:`meobot.application.pr_channel_sync_service`'s job, and the split is what
lets every test in this repository run without contacting Google.
"""

from __future__ import annotations

from meobot.integrations.youtube.constants import (
    YOUTUBE_SCOPES,
    YouTubeOAuthEndpoints,
)
from meobot.integrations.youtube.errors import YouTubeApiError
from meobot.integrations.youtube.provider import YouTubeChannelMetricsProvider

__all__: list[str] = [
    "YOUTUBE_SCOPES",
    "YouTubeApiError",
    "YouTubeChannelMetricsProvider",
    "YouTubeOAuthEndpoints",
]
