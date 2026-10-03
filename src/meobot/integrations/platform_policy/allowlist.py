"""Which URLs the policy fetcher is allowed to open, and nothing else.

Step 1F.1. An allowlist rather than a blocklist, and an exact-host allowlist
rather than a suffix match: ``facebook.com.evil.test`` ends with the string
``facebook.com`` and is not Facebook, so membership is tested against a set of
whole hostnames.

This is the SSRF boundary. Every URL the fetcher opens - the registry's
canonical URL, and every redirect target - passes through
:func:`assert_approved_url`. There is deliberately no code path anywhere in this
application that fetches a URL a user supplied: policy URLs come from
``pr_platform_policy_sources`` rows, which an operator seeds.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from meobot.core.errors import ValidationError

#: Official Meta and TikTok properties, and nothing else. Not blogs, not
#: aggregators, not search engines, not cached snippets, not mirrors. An
#: official source that cannot be reached is a refresh failure, never a reason
#: to read somebody's summary of it.
APPROVED_HOSTS: frozenset[str] = frozenset(
    {
        # Meta
        "facebook.com",
        "www.facebook.com",
        "transparency.meta.com",
        # TikTok
        "tiktok.com",
        "www.tiktok.com",
        "ads.tiktok.com",
    }
)


class PolicyUrlRejectedError(ValidationError):
    """A URL the policy fetcher refuses to open."""


def is_approved_url(url: str) -> bool:
    """Whether the fetcher may open this URL. See :func:`assert_approved_url`."""
    try:
        assert_approved_url(url)
    except PolicyUrlRejectedError:
        return False
    return True


def assert_approved_url(url: str) -> str:
    """Return ``url`` if the fetcher may open it, or raise.

    Four refusals, in the order a URL fails them:

    * **not https.** Policy text read over plain HTTP is text somebody on the
      path may have written;
    * **credentials in the URL.** ``https://user:pass@host/`` is both a
      credential in a database column and a way to disguise the real host;
    * **a host that is not on the allowlist.** Exact match, never a suffix;
    * **a port.** An approved host on an unusual port is a different service.

    Applied to the registry's URL *and* to every redirect target, which is what
    stops an approved page from redirecting the fetcher somewhere it may not go.
    """
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise PolicyUrlRejectedError(
            "Policy sources must be https",
            details={"reason": "scheme_not_https", "scheme": parts.scheme},
        )
    if parts.username or parts.password:
        raise PolicyUrlRejectedError(
            "Policy source URLs must not carry credentials",
            details={"reason": "credentials_in_url"},
        )
    host = (parts.hostname or "").lower()
    if host not in APPROVED_HOSTS:
        raise PolicyUrlRejectedError(
            "Policy source host is not an approved official domain",
            details={"reason": "host_not_approved", "host": host},
        )
    if parts.port is not None and parts.port != 443:
        raise PolicyUrlRejectedError(
            "Policy sources are served on the default https port",
            details={"reason": "unexpected_port", "port": parts.port},
        )
    return url


__all__ = ["APPROVED_HOSTS", "PolicyUrlRejectedError", "assert_approved_url", "is_approved_url"]
