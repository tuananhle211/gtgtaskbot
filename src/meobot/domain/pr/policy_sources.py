"""Which official pages MeoBot ingests, as data.

Step 1F.1. The seed for ``pr_platform_policy_sources``. **No policy prose lives
here** - only the addresses of pages that carry it, which is the difference
between configuration and a copy of Meta's rulebook checked into git.

Why sub-pages rather than the obvious top-level URLs
---------------------------------------------------

Both platforms put an index at the top of their policy tree and the actual rules
one level down. Measured against the real sites, with the deterministic
extractor in :mod:`meobot.integrations.platform_policy.normalizer`:

===================================================  ===========  ==============
Page                                                 Extracted    What it is
===================================================  ===========  ==============
``transparency.meta.com/policies/community-standards/``  ~4.6k     introduction
``…/community-standards/hate-speech/``                  ~10.3k     the rules
``…/community-standards/bullying-harassment/``          ~11.6k     the rules
``transparency.meta.com/policies/ad-standards/``         ~35.9k    the rules
``ads.tiktok.com/help/article/tiktok-advertising-policies``  ~1.8k  contents list
``tiktok.com/community-guidelines/en/``                  ~685k     the rules
===================================================  ===========  ==============

Registering the two index pages as policy content would have produced a pack
built from a table of contents: it would exist, activate, and ground production
reviews in navigation. So they are registered
:attr:`~meobot.domain.pr.models.PrPolicySourceRole.DISCOVERY_INDEX` - they may
find sub-pages and contribute no rules of their own.

Meta's advertising standards page is the exception that proves the rule: it is a
top-level URL *and* substantive, so it is content.

TikTok's advertising policies
-----------------------------

The advertising index links to 36 official articles. Running the bounded
discovery against it and fetching each - politely, see below - every one returns
substantive text, from 750 to 113,390 characters. So "substantive" was not the
filter that mattered; **relevance** was.

The 20 seeded here are the ones that constrain **ad creative content**, which is
what a PR text review is judged against. Sixteen discovered articles are
deliberately *not* seeded, and none of them because they are thin:

* advertiser eligibility - government agency, state-affiliated media, NGO;
* onboarding and tooling - "first things to note", advertiser tools and terms,
  the Promote product's own policy page;
* regional and procedural lists - market-specific approval requirements,
  certificate requirements, the EU self-regulatory code list, branded-content
  country requirements, housing/employment/credit;
* practice rather than creative - data collection standards, the IP
  infringement *rules* page (the policy page is seeded), and the
  after-conversion landing-page pages.

They remain discoverable from the index, so an operator can register any of
them with no code change - which is the point of keeping the registry in the
database.

Rate limits are real
--------------------

Fetching all 36 back to back earned a ``403`` for the whole IP, including pages
that had answered seconds earlier, and it took several minutes to clear. That is
why :data:`~meobot.integrations.platform_policy.fetcher.MIN_REQUEST_INTERVAL_SECONDS`
exists: the fetcher waits between requests to one host rather than retrying
harder or disguising itself. With 21 TikTok sources registered, a refresh now
takes about a minute and does not get the deployment blocked.

Discovery is bounded, not a crawler
-----------------------------------

An index may only yield links that are **under its own path prefix on its own
approved host** - see ``discovery_prefix``. ``ads.tiktok.com/help/article/…``
discovers other help articles and cannot wander into the TikTok app, a login
flow, or a marketing page. One level, no recursion, no sitemap, no search. The
allowlist still applies on top of that, so even a bug in the prefix logic cannot
reach a host this project does not approve.
"""

from __future__ import annotations

from dataclasses import dataclass

from meobot.domain.pr.models import (
    PrPolicyIngestionMethod,
    PrPolicyScope,
    PrPolicySourceRole,
)


@dataclass(frozen=True, slots=True)
class PolicySourceSeed:
    """One registry row, before it is a row."""

    platform_code: str
    policy_scope: PrPolicyScope
    source_role: PrPolicySourceRole
    source_family: str
    name: str
    canonical_url: str
    ingestion_method: PrPolicyIngestionMethod = PrPolicyIngestionMethod.FETCH
    #: For a discovery index: the prefix a discovered link must start with.
    #: Absent for content sources, which discover nothing.
    discovery_prefix: str | None = None


#: The seed registry. Verified reachable and substantive against the live sites
#: on 2026-08-09; a URL that later moves is corrected in the database, not here.
#:
#: ``source_family`` is the stable handle ops commands use, so it may not change
#: even when a URL does.
POLICY_SOURCE_SEEDS: tuple[PolicySourceSeed, ...] = (
    # --- Meta / Facebook ---------------------------------------------------
    PolicySourceSeed(
        platform_code="FACEBOOK",
        policy_scope=PrPolicyScope.COMMUNITY_STANDARDS,
        source_role=PrPolicySourceRole.DISCOVERY_INDEX,
        source_family="META_COMMUNITY_STANDARDS_INDEX",
        name="Meta Community Standards (index)",
        canonical_url="https://transparency.meta.com/policies/community-standards/",
        discovery_prefix="https://transparency.meta.com/policies/community-standards/",
    ),
    PolicySourceSeed(
        platform_code="FACEBOOK",
        policy_scope=PrPolicyScope.COMMUNITY_STANDARDS,
        source_role=PrPolicySourceRole.POLICY_CONTENT,
        source_family="META_CS_HATE_SPEECH",
        name="Meta Community Standards — Hateful conduct",
        canonical_url="https://transparency.meta.com/policies/community-standards/hateful-conduct/",
    ),
    PolicySourceSeed(
        platform_code="FACEBOOK",
        policy_scope=PrPolicyScope.COMMUNITY_STANDARDS,
        source_role=PrPolicySourceRole.POLICY_CONTENT,
        source_family="META_CS_BULLYING_HARASSMENT",
        name="Meta Community Standards — Bullying and harassment",
        canonical_url=(
            "https://transparency.meta.com/policies/community-standards/bullying-harassment/"
        ),
    ),
    PolicySourceSeed(
        platform_code="FACEBOOK",
        policy_scope=PrPolicyScope.COMMUNITY_STANDARDS,
        source_role=PrPolicySourceRole.POLICY_CONTENT,
        source_family="META_CS_FRAUD_DECEPTION",
        name="Meta Community Standards — Fraud and scams",
        canonical_url=(
            "https://transparency.meta.com/policies/community-standards/fraud-and-scams/"
        ),
    ),
    PolicySourceSeed(
        platform_code="FACEBOOK",
        policy_scope=PrPolicyScope.COMMUNITY_STANDARDS,
        source_role=PrPolicySourceRole.POLICY_CONTENT,
        source_family="META_CS_REGULATED_GOODS",
        name="Meta Community Standards — Restricted goods and services",
        canonical_url=(
            "https://transparency.meta.com/policies/community-standards/restricted-goods-services/"
        ),
    ),
    # Top-level *and* substantive - the exception to the index rule.
    PolicySourceSeed(
        platform_code="FACEBOOK",
        policy_scope=PrPolicyScope.ADVERTISING_STANDARDS,
        source_role=PrPolicySourceRole.POLICY_CONTENT,
        source_family="META_ADVERTISING_STANDARDS",
        name="Meta Advertising Standards",
        canonical_url="https://transparency.meta.com/policies/ad-standards/",
    ),
    # --- TikTok ------------------------------------------------------------
    PolicySourceSeed(
        platform_code="TIKTOK",
        policy_scope=PrPolicyScope.COMMUNITY_STANDARDS,
        source_role=PrPolicySourceRole.POLICY_CONTENT,
        source_family="TIKTOK_COMMUNITY_GUIDELINES",
        name="TikTok Community Guidelines",
        canonical_url="https://www.tiktok.com/community-guidelines/en/",
    ),
    PolicySourceSeed(
        platform_code="TIKTOK",
        policy_scope=PrPolicyScope.ADVERTISING_STANDARDS,
        source_role=PrPolicySourceRole.DISCOVERY_INDEX,
        source_family="TIKTOK_ADVERTISING_POLICIES_INDEX",
        name="TikTok Advertising Policies (index)",
        canonical_url="https://ads.tiktok.com/help/article/tiktok-advertising-policies",
        discovery_prefix="https://ads.tiktok.com/help/article/",
    ),
    # The ad *creative content* policies the index links to, found by running
    # the bounded discovery against it. Every one measured substantive against
    # the live site - see the table in the module docstring.
    PolicySourceSeed(
        platform_code="TIKTOK",
        policy_scope=PrPolicyScope.ADVERTISING_STANDARDS,
        source_role=PrPolicySourceRole.POLICY_CONTENT,
        source_family="TIKTOK_ADS_AD_FORMAT",
        name="TikTok Advertising Policies — Ad format and functionality",
        canonical_url="https://ads.tiktok.com/help/article/tiktok-ads-policy-ad-format-and-functionality",
    ),
    PolicySourceSeed(
        platform_code="TIKTOK",
        policy_scope=PrPolicyScope.ADVERTISING_STANDARDS,
        source_role=PrPolicySourceRole.POLICY_CONTENT,
        source_family="TIKTOK_ADS_ADULT_CONTENT",
        name="TikTok Advertising Policies — Adult content",
        canonical_url="https://ads.tiktok.com/help/article/tiktok-ads-policy-adult-content",
    ),
    PolicySourceSeed(
        platform_code="TIKTOK",
        policy_scope=PrPolicyScope.ADVERTISING_STANDARDS,
        source_role=PrPolicySourceRole.POLICY_CONTENT,
        source_family="TIKTOK_ADS_ALCOHOL",
        name="TikTok Advertising Policies — Alcohol",
        canonical_url="https://ads.tiktok.com/help/article/tiktok-ads-policy-alcohol",
    ),
    PolicySourceSeed(
        platform_code="TIKTOK",
        policy_scope=PrPolicyScope.ADVERTISING_STANDARDS,
        source_role=PrPolicySourceRole.POLICY_CONTENT,
        source_family="TIKTOK_ADS_ANIMALS_ENVIRONMENT",
        name="TikTok Advertising Policies — Animals and environment",
        canonical_url="https://ads.tiktok.com/help/article/tiktok-ads-policy-animals-and-environment",
    ),
    PolicySourceSeed(
        platform_code="TIKTOK",
        policy_scope=PrPolicyScope.ADVERTISING_STANDARDS,
        source_role=PrPolicySourceRole.POLICY_CONTENT,
        source_family="TIKTOK_ADS_COMMERCE",
        name="TikTok Advertising Policies — Commerce policies",
        canonical_url="https://ads.tiktok.com/help/article/commerce-policies",
    ),
    PolicySourceSeed(
        platform_code="TIKTOK",
        policy_scope=PrPolicyScope.ADVERTISING_STANDARDS,
        source_role=PrPolicySourceRole.POLICY_CONTENT,
        source_family="TIKTOK_ADS_DANGEROUS_PRODUCTS",
        name="TikTok Advertising Policies — Dangerous products or services",
        canonical_url="https://ads.tiktok.com/help/article/tiktok-ads-policy-dangerous-products-or-services",
    ),
    PolicySourceSeed(
        platform_code="TIKTOK",
        policy_scope=PrPolicyScope.ADVERTISING_STANDARDS,
        source_role=PrPolicySourceRole.POLICY_CONTENT,
        source_family="TIKTOK_ADS_DECEPTIVE_PRACTICES",
        name="TikTok Advertising Policies — Deceptive practices",
        canonical_url="https://ads.tiktok.com/help/article/tiktok-ads-policy-deceptive-practices",
    ),
    PolicySourceSeed(
        platform_code="TIKTOK",
        policy_scope=PrPolicyScope.ADVERTISING_STANDARDS,
        source_role=PrPolicySourceRole.POLICY_CONTENT,
        source_family="TIKTOK_ADS_DISCRIMINATION",
        name="TikTok Advertising Policies — Discrimination, harassment and bullying",
        canonical_url="https://ads.tiktok.com/help/article/discrimination-harassment-bullying",
    ),
    PolicySourceSeed(
        platform_code="TIKTOK",
        policy_scope=PrPolicyScope.ADVERTISING_STANDARDS,
        source_role=PrPolicySourceRole.POLICY_CONTENT,
        source_family="TIKTOK_ADS_FINANCIAL_SERVICES",
        name="TikTok Advertising Policies — Financial services",
        canonical_url="https://ads.tiktok.com/help/article/tiktok-ads-policy-financial-services",
    ),
    PolicySourceSeed(
        platform_code="TIKTOK",
        policy_scope=PrPolicyScope.ADVERTISING_STANDARDS,
        source_role=PrPolicySourceRole.POLICY_CONTENT,
        source_family="TIKTOK_ADS_GAMBLING",
        name="TikTok Advertising Policies — Gambling and games",
        canonical_url="https://ads.tiktok.com/help/article/tiktok-ads-policy-gambling-and-games",
    ),
    PolicySourceSeed(
        platform_code="TIKTOK",
        policy_scope=PrPolicyScope.ADVERTISING_STANDARDS,
        source_role=PrPolicySourceRole.POLICY_CONTENT,
        source_family="TIKTOK_ADS_HEALTHCARE",
        name="TikTok Advertising Policies — Healthcare and pharmaceuticals",
        canonical_url="https://ads.tiktok.com/help/article/tiktok-ads-policy-healthcare-pharmaceuticals",
    ),
    PolicySourceSeed(
        platform_code="TIKTOK",
        policy_scope=PrPolicyScope.ADVERTISING_STANDARDS,
        source_role=PrPolicySourceRole.POLICY_CONTENT,
        source_family="TIKTOK_ADS_IP_INFRINGEMENT",
        name="TikTok Advertising Policies — Intellectual property infringement",
        canonical_url="https://ads.tiktok.com/help/article/tiktok-ads-policy-intellectual-property-infringement",
    ),
    PolicySourceSeed(
        platform_code="TIKTOK",
        policy_scope=PrPolicyScope.ADVERTISING_STANDARDS,
        source_role=PrPolicySourceRole.POLICY_CONTENT,
        source_family="TIKTOK_ADS_MISINFORMATION",
        name="TikTok Advertising Policies — Misinformation",
        canonical_url="https://ads.tiktok.com/help/article/tiktok-ads-policy-misinformation",
    ),
    PolicySourceSeed(
        platform_code="TIKTOK",
        policy_scope=PrPolicyScope.ADVERTISING_STANDARDS,
        source_role=PrPolicySourceRole.POLICY_CONTENT,
        source_family="TIKTOK_ADS_MISLEADING_CONTENT",
        name="TikTok Advertising Policies — Misleading and false content",
        canonical_url="https://ads.tiktok.com/help/article/tiktok-ads-policy-misleading-and-false-content",
    ),
    PolicySourceSeed(
        platform_code="TIKTOK",
        policy_scope=PrPolicyScope.ADVERTISING_STANDARDS,
        source_role=PrPolicySourceRole.POLICY_CONTENT,
        source_family="TIKTOK_ADS_OTHER_PRODUCTS",
        name="TikTok Advertising Policies — Other products and services",
        canonical_url="https://ads.tiktok.com/help/article/tiktok-ads-policy-other-products-and-services",
    ),
    PolicySourceSeed(
        platform_code="TIKTOK",
        policy_scope=PrPolicyScope.ADVERTISING_STANDARDS,
        source_role=PrPolicySourceRole.POLICY_CONTENT,
        source_family="TIKTOK_ADS_POLITICS",
        name="TikTok Advertising Policies — Politics, government and elections",
        canonical_url="https://ads.tiktok.com/help/article/tiktok-ads-policy-politics-government-and-elections",
    ),
    PolicySourceSeed(
        platform_code="TIKTOK",
        policy_scope=PrPolicyScope.ADVERTISING_STANDARDS,
        source_role=PrPolicySourceRole.POLICY_CONTENT,
        source_family="TIKTOK_ADS_SUICIDE_SELF_HARM",
        name="TikTok Advertising Policies — Suicide and self-harm",
        canonical_url="https://ads.tiktok.com/help/article/tiktok-ads-policy-suicide-and-self-harm",
    ),
    PolicySourceSeed(
        platform_code="TIKTOK",
        policy_scope=PrPolicyScope.ADVERTISING_STANDARDS,
        source_role=PrPolicySourceRole.POLICY_CONTENT,
        source_family="TIKTOK_ADS_VIOLENCE",
        name="TikTok Advertising Policies — Violence and dangerous activities",
        canonical_url="https://ads.tiktok.com/help/article/tiktok-ads-policy-violence-and-dangerous-activities",
    ),
    PolicySourceSeed(
        platform_code="TIKTOK",
        policy_scope=PrPolicyScope.ADVERTISING_STANDARDS,
        source_role=PrPolicySourceRole.POLICY_CONTENT,
        source_family="TIKTOK_ADS_WEIGHT_MANAGEMENT",
        name="TikTok Advertising Policies — Weight management",
        canonical_url="https://ads.tiktok.com/help/article/tiktok-ads-policy-weight-management",
    ),
    PolicySourceSeed(
        platform_code="TIKTOK",
        policy_scope=PrPolicyScope.ADVERTISING_STANDARDS,
        source_role=PrPolicySourceRole.POLICY_CONTENT,
        source_family="TIKTOK_ADS_YOUTH_SAFETY",
        name="TikTok Advertising Policies — Youth safety",
        canonical_url="https://ads.tiktok.com/help/article/tiktok-ads-policy-youth-safety",
    ),
)


def seeds_for(platform_code: str) -> tuple[PolicySourceSeed, ...]:
    return tuple(seed for seed in POLICY_SOURCE_SEEDS if seed.platform_code == platform_code)


__all__ = ["POLICY_SOURCE_SEEDS", "PolicySourceSeed", "seeds_for"]
