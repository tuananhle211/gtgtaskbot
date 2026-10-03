"""Google endpoints and scopes, written down once and never taken from input.

Every URL the connector will ever contact is in this module as a literal. That
is the SSRF answer: there is no code path where a channel row, a request body or
a redirect target can decide where a request goes. In particular the connector
**never fetches ``pr_channels.url``** - a person typed that, and a person can
type ``http://169.254.169.254``.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The scopes MeoBot asks for, and nothing else.
#:
#: Both are **read-only**, and that is a deliberate refusal rather than an
#: oversight. Google offers ``https://www.googleapis.com/auth/youtube`` and
#: ``youtube.force-ssl``, either of which would also work here and either of
#: which would let the holder upload, edit and delete videos on the connected
#: channel. MeoBot reads numbers. A consent screen that asks a marketing manager
#: to grant delete rights so a dashboard can show a follower count is asking for
#: the wrong thing, and the day something goes wrong the blast radius is the
#: difference between these two lines and those.
#:
#: * ``youtube.readonly`` - YouTube Data API v3, for the channel's identity and
#:   its ``statistics`` block (subscriber, video and cumulative view counts);
#: * ``yt-analytics.readonly`` - YouTube Analytics API v2, for owner-authorized
#:   date-windowed reports. The Data API cannot produce "views over the last 30
#:   days" at all, which is why the second scope is needed rather than merely
#:   convenient.
#:
#: Not requested: ``yt-analytics-monetary.readonly`` (revenue - MeoBot has no
#: use for it and it is the most sensitive thing on offer), ``youtubepartner``,
#: and every write scope.
YOUTUBE_SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
)


@dataclass(frozen=True, slots=True)
class YouTubeOAuthEndpoints:
    """Google's OAuth and API hosts.

    A frozen dataclass with defaults rather than loose module constants so a
    test can substitute a local base URL without monkey-patching a module, while
    production has no configuration surface that could point it elsewhere:
    nothing reads these from the environment.
    """

    authorize: str = "https://accounts.google.com/o/oauth2/v2/auth"
    token: str = "https://oauth2.googleapis.com/token"  # noqa: S105 - a URL, not a token
    revoke: str = "https://oauth2.googleapis.com/revoke"
    data_api: str = "https://www.googleapis.com/youtube/v3"
    analytics_api: str = "https://youtubeanalytics.googleapis.com/v2"


DEFAULT_ENDPOINTS = YouTubeOAuthEndpoints()

#: Seconds. Generous enough for Analytics, which is slower than the Data API,
#: and bounded because a hung socket must not hold a worker slot until Celery's
#: task time limit.
DEFAULT_TIMEOUT_SECONDS = 20.0

#: How many days behind "today" YouTube Analytics is assumed to be settled.
#:
#: Analytics data for the current day is always incomplete and the previous day
#: is often still moving. Two days is the conservative choice that makes a
#: report reproducible: the same 30-day window queried twice returns the same
#: numbers, which is what makes the idempotency fingerprint meaningful rather
#: than noise.
ANALYTICS_LAG_DAYS = 2

__all__: list[str] = [
    "ANALYTICS_LAG_DAYS",
    "DEFAULT_ENDPOINTS",
    "DEFAULT_TIMEOUT_SECONDS",
    "YOUTUBE_SCOPES",
    "YouTubeOAuthEndpoints",
]
