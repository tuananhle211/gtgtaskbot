"""Fetch the real official policy sources. Run explicitly; never in CI.

Step 1F.1. The unit suite must not depend on Meta or TikTok being up, so every
parser and fetcher test there uses synthetic markup. This script is the other
half: proof that the registry's URLs still resolve and still carry substantive
policy text.

    uv run python scripts/policy_smoke.py

It writes nothing to the database. It fetches, normalizes, and reports how much
text each source yielded against ``MIN_USEFUL_CHARS`` - which is the threshold
``PrPolicySourceService`` uses to refuse a snapshot. A source reported ``THIN``
here would be refused there, and is a signal that a page has gone
client-rendered and needs either a new sub-page URL or an operator import.

An anti-bot response is reported as a failure. It is not a reason to pretend to
be a browser.
"""

from __future__ import annotations

import asyncio

from meobot.domain.pr.models import PrPolicySourceRole
from meobot.domain.pr.policy_sources import POLICY_SOURCE_SEEDS
from meobot.integrations.platform_policy import MIN_USEFUL_CHARS, PolicySourceFetcher


async def main() -> int:
    # The default per-host pacing applies; with 21 TikTok sources registered
    # this takes about a minute and does not get the host blocked.
    fetcher = PolicySourceFetcher()
    failures = 0
    for seed in POLICY_SOURCE_SEEDS:
        role = "index  " if seed.source_role is PrPolicySourceRole.DISCOVERY_INDEX else "content"
        try:
            page = await fetcher.fetch(seed.canonical_url)
        except Exception as exc:
            code = getattr(exc, "code", None) or type(exc).__name__
            print(f"FAILED   {role} {seed.source_family:<36} {code}")
            failures += 1
            continue

        # An index is *expected* to be thin; that is why it contributes no rules.
        expected_thin = seed.source_role is PrPolicySourceRole.DISCOVERY_INDEX
        thin = page.length < MIN_USEFUL_CHARS
        if thin and not expected_thin:
            state = "THIN   "
            failures += 1
        else:
            state = "ok     "
        print(
            f"{state}  {role} {seed.source_family:<36} "
            f"{page.length:>8} chars  {page.content_sha256[:12]}"
        )
        if page.final_url != seed.canonical_url:
            print(f"{'':<10}{'':<8} redirected to {page.final_url}")
    print(f"\n{len(POLICY_SOURCE_SEEDS)} source(s), {failures} problem(s).")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
