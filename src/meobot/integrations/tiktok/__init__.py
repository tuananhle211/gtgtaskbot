"""TikTok integration: Login Kit for consent, the Display API for reading.

Step 1F.2.6 turned this package from a milestone-1 placeholder - a Protocol, a
fake, and a stub that only raised - into a real connector. The metrics Protocol
and its fake are kept because the milestone-1 publishing roadmap still refers to
them; what is new is everything that talks to ``open.tiktokapis.com``.

Read :mod:`meobot.integrations.tiktok.constants` first. It says which TikTok
product this is and, more usefully, which two it deliberately is not.
"""

from meobot.integrations.tiktok.client import TikTokApiClient, TikTokTokens, VideoPage
from meobot.integrations.tiktok.constants import TIKTOK_SCOPES, TikTokEndpoints
from meobot.integrations.tiktok.errors import TikTokApiError
from meobot.integrations.tiktok.metrics import (
    FakeTikTokMetricsClient,
    TikTokMetricsClient,
    TikTokVideoMetrics,
)
from meobot.integrations.tiktok.probe import (
    AccountProbeReport,
    FieldProbeResult,
    ProbeVerdict,
    TikTokCapabilityProbe,
)
from meobot.integrations.tiktok.provider import (
    TikTokAccountOverview,
    TikTokChannelMetricsProvider,
    TikTokRecentVideos,
    TikTokVideoSummary,
)

__all__ = [
    "TIKTOK_SCOPES",
    "AccountProbeReport",
    "FakeTikTokMetricsClient",
    "FieldProbeResult",
    "ProbeVerdict",
    "TikTokAccountOverview",
    "TikTokApiClient",
    "TikTokApiError",
    "TikTokCapabilityProbe",
    "TikTokChannelMetricsProvider",
    "TikTokEndpoints",
    "TikTokMetricsClient",
    "TikTokRecentVideos",
    "TikTokTokens",
    "TikTokVideoMetrics",
    "TikTokVideoSummary",
    "VideoPage",
]
