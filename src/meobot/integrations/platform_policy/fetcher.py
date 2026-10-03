"""The one HTTP client that talks to Meta and TikTok, under strict bounds.

Step 1F.1. Everything here exists to make one operation safe: open a URL that
came out of ``pr_platform_policy_sources`` and read policy text from it.

```
registry row ─→ assert_approved_url ─→ GET (redirects checked one at a time)
                                          │
                        bounded size, bounded time, text/html only
                                          ▼
                              normalized document + sha256
```

What it will not do
-------------------

* **fetch a URL a user supplied.** There is no route, tool or task that passes
  caller input here. URLs come from registry rows an operator seeded, and every
  one is re-checked against the allowlist at call time anyway;
* **follow a redirect off an approved host.** Redirects are followed manually,
  one hop at a time, and each ``Location`` goes through the allowlist. That is
  the SSRF hole an ``allow_redirects=True`` would leave open;
* **send credentials.** No cookie jar, no auth header, no API key. These are
  public pages and MeoBot reads them as an anonymous client;
* **work around anti-bot measures.** A ``403``, a CAPTCHA interstitial or a
  challenge page is reported as a fetch failure and the active pack is left
  alone. Circumventing it would be both a terms violation and a way to end up
  ingesting a challenge page as though it were policy;
* **run during a content review.** ``PrAiReviewExecutor`` does not import this
  module, and a test asserts the whole package stays out of the review path.

Client-rendered sources
-----------------------

Some official pages - ``transparency.meta.com`` in particular - render their
policy in the browser and serve an HTML shell containing no prose. This fetcher
returns what the host actually sent; the caller sees a document too short to be
policy and refuses to snapshot it. That is deliberate. The alternative was a
headless browser in the worker image or reading somebody's copy of Meta's rules,
and the chosen answer is neither: those sources are registered as
``OPERATOR_IMPORT`` and their text is supplied by an operator, hashed and
provenance-tracked exactly like a fetched one. See
``docs/pr/STEP_1F1_PLATFORM_POLICY.md``.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import datetime
from time import monotonic
from urllib.parse import urlsplit

import httpx

from meobot.core.errors import MeoBotError
from meobot.core.logging import get_logger
from meobot.core.time import utcnow
from meobot.integrations.platform_policy.allowlist import (
    PolicyUrlRejectedError,
    assert_approved_url,
)
from meobot.integrations.platform_policy.normalizer import (
    PARSER_VERSION,
    normalize_policy_html,
)

logger = get_logger(__name__)

#: 8 MiB. TikTok's guidelines page is about 2.6 MiB of HTML, so this is roughly
#: three times the largest real source and still small enough that a
#: misconfigured URL cannot exhaust the worker's memory.
MAX_RESPONSE_BYTES = 8 * 1024 * 1024

#: Redirect hops. Real official URLs use one or two; more than this is a loop
#: or a chain worth refusing.
MAX_REDIRECTS = 4

#: Seconds. Generous, because these pages are large and this runs on a schedule
#: with nobody waiting - but bounded, because a hung socket must not hold a
#: worker slot until the task time limit.
DEFAULT_TIMEOUT_SECONDS = 30.0

#: Seconds to leave between requests to the same host. Measured, not guessed:
#: fetching 36 ``ads.tiktok.com`` articles back to back earned a ``403`` for the
#: whole IP, including pages that had answered moments earlier, and it took
#: several minutes to clear. A daily refresh over twenty registered sources would
#: reproduce that on the deployment host every night.
#:
#: This is politeness, not circumvention: the fetcher waits rather than
#: retrying harder or disguising itself.
MIN_REQUEST_INTERVAL_SECONDS = 3.0

#: Deterministic and honest about who is asking. Not a browser string: pretending
#: to be Chrome would be the first step of the anti-bot circumvention this
#: module refuses to do.
USER_AGENT = "MeoBot-PolicyIngest/1.0 (+internal PR policy review; contact: operator)"

#: Only markup. A PDF or a JSON API would need a parser this module does not
#: have, and accepting one would mean hashing bytes nobody can read.
ALLOWED_CONTENT_TYPES = ("text/html", "application/xhtml+xml")


class PolicyFetchError(MeoBotError):
    """An official source could not be read.

    Always safe to surface and to store: ``code`` is a stable machine string and
    the message names no header, cookie or response body.
    """

    code = "policy_fetch_error"


@dataclass(frozen=True, slots=True)
class FetchedPolicyPage:
    """One successful read of one official page."""

    #: The URL actually read, after redirects. Recorded on the snapshot, because
    #: it may differ from the registry's canonical URL.
    final_url: str
    fetched_at: datetime
    normalized_content: str
    content_sha256: str
    parser_version: str
    #: The markup as served, for a discovery index to read links out of.
    #: **Never persisted** - a snapshot stores ``normalized_content`` and
    #: nothing else, so no scripts, chrome or analytics reach the database.
    #: Empty for an operator import, which has no markup.
    raw_markup: str = ""

    @property
    def length(self) -> int:
        return len(self.normalized_content)


def content_hash(normalized: str) -> str:
    """SHA-256 of the normalized document.

    Over the *normalized* text rather than the raw bytes: a CDN that changes a
    whitespace run or an inline nonce must not look like a policy edit, and the
    normalized document is what a rule is actually built from.
    """
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class PolicySourceFetcher:
    """Reads official policy pages. Nothing else opens a socket for the PR module.

    Args:
        timeout: Per-request seconds.
        max_bytes: Response cap. A body larger than this is refused rather than
            truncated - a half-read policy page would hash stably and be wrong.
    """

    def __init__(
        self,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_bytes: int = MAX_RESPONSE_BYTES,
        min_interval: float = MIN_REQUEST_INTERVAL_SECONDS,
    ) -> None:
        self._timeout = timeout
        self._max_bytes = max_bytes
        self._min_interval = min_interval
        self._last_request_at: dict[str, float] = {}

    async def fetch(self, url: str) -> FetchedPolicyPage:
        """Read one approved URL and return its normalized document.

        Raises:
            PolicyUrlRejectedError: The URL, or a redirect target, is not an approved
                official https address.
            PolicyFetchError: The host refused, timed out, answered with the
                wrong content type, or sent a body over the cap.
        """
        assert_approved_url(url)
        await self._pace(url)
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en",
        }
        current = url
        try:
            # ``follow_redirects=False`` on purpose: each hop is checked against
            # the allowlist before it is taken. Letting httpx follow them would
            # mean an approved page could send this client anywhere.
            async with httpx.AsyncClient(
                timeout=self._timeout, follow_redirects=False, headers=headers
            ) as client:
                for _ in range(MAX_REDIRECTS + 1):
                    response = await client.get(current)
                    if response.is_redirect:
                        location = response.headers.get("location", "")
                        current = assert_approved_url(str(response.url.join(location)))
                        continue
                    return self._read(response, current)
        except PolicyUrlRejectedError:
            raise
        except httpx.HTTPError as exc:
            # The exception's text can carry a URL and connection detail; only
            # the class name is kept, and only as a bounded code.
            raise PolicyFetchError(
                "Không đọc được nguồn chính sách chính thức.",
                details={"reason": "transport_error", "kind": type(exc).__name__},
            ) from exc

        raise PolicyFetchError(
            "Nguồn chính sách chuyển hướng quá nhiều lần.",
            details={"reason": "too_many_redirects", "max_redirects": MAX_REDIRECTS},
        )

    async def _pace(self, url: str) -> None:
        """Wait, if this host was called too recently.

        Per host rather than globally, so a Meta fetch does not wait behind a
        TikTok one. There is nothing clever here - it sleeps.
        """
        host = urlsplit(url).hostname or ""
        previous = self._last_request_at.get(host)
        now = monotonic()
        if previous is not None:
            wait = self._min_interval - (now - previous)
            if wait > 0:
                await asyncio.sleep(wait)
        self._last_request_at[host] = monotonic()

    def _read(self, response: httpx.Response, url: str) -> FetchedPolicyPage:
        """Validate one final response and normalize it."""
        if response.status_code != 200:
            # Includes 403 and challenge interstitials. Reported, never
            # worked around.
            raise PolicyFetchError(
                "Nguồn chính sách chính thức trả về lỗi.",
                details={"reason": "http_status", "status": response.status_code},
            )

        media_type = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
        if media_type not in ALLOWED_CONTENT_TYPES:
            raise PolicyFetchError(
                "Nguồn chính sách trả về định dạng không hỗ trợ.",
                details={"reason": "content_type", "content_type": media_type},
            )

        body = response.content
        if len(body) > self._max_bytes:
            raise PolicyFetchError(
                "Nguồn chính sách vượt quá kích thước cho phép.",
                details={"reason": "too_large", "bytes": len(body), "limit": self._max_bytes},
            )

        markup = body.decode(response.encoding or "utf-8", "replace")
        normalized = normalize_policy_html(markup)
        page = FetchedPolicyPage(
            final_url=url,
            fetched_at=utcnow(),
            normalized_content=normalized,
            content_sha256=content_hash(normalized),
            parser_version=PARSER_VERSION,
            raw_markup=markup,
        )
        logger.info(
            "policy_source_fetched",
            extra={
                "policy_url": url,
                "content_sha256": page.content_sha256,
                "normalized_chars": page.length,
                "parser_version": page.parser_version,
            },
        )
        return page


__all__ = [
    "ALLOWED_CONTENT_TYPES",
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_REDIRECTS",
    "MAX_RESPONSE_BYTES",
    "USER_AGENT",
    "FetchedPolicyPage",
    "PolicyFetchError",
    "PolicySourceFetcher",
    "content_hash",
]
