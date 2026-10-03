"""Fetching and normalizing official platform policy.

Step 1F.1. The **only** place in this codebase that makes an HTTP request to
Meta or TikTok. It runs during a scheduled refresh or an ops command, and never
during a content review: ``PrAiReviewExecutor`` does not import this package,
and a test asserts that.
"""

from meobot.integrations.platform_policy.allowlist import (
    APPROVED_HOSTS,
    PolicyUrlRejectedError,
    assert_approved_url,
    is_approved_url,
)
from meobot.integrations.platform_policy.fetcher import (
    MAX_RESPONSE_BYTES,
    FetchedPolicyPage,
    PolicyFetchError,
    PolicySourceFetcher,
)
from meobot.integrations.platform_policy.normalizer import (
    MIN_USEFUL_CHARS,
    PARSER_VERSION,
    PolicySection,
    normalize_policy_html,
    normalize_policy_text,
    split_sections,
)

__all__ = [
    "APPROVED_HOSTS",
    "MAX_RESPONSE_BYTES",
    "MIN_USEFUL_CHARS",
    "PARSER_VERSION",
    "FetchedPolicyPage",
    "PolicyFetchError",
    "PolicySection",
    "PolicySourceFetcher",
    "PolicyUrlRejectedError",
    "assert_approved_url",
    "is_approved_url",
    "normalize_policy_html",
    "normalize_policy_text",
    "split_sections",
]
