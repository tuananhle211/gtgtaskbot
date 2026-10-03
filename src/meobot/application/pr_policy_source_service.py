"""Keeping the official policy snapshots current, without changing what reviews use.

Step 1F.1. This service owns ``pr_platform_policy_sources`` and
``pr_platform_policy_snapshots``. It fetches, it hashes, it appends - and it
deliberately **never touches a policy pack**.

```
seed()      registry rows, from the source manifest (data, not prose)
refresh()   fetch each enabled source ─→ hash ─→ new snapshot only if changed
discover()  an index yields sub-pages under its own prefix, and no further
```

Why refresh cannot change production
------------------------------------

An official page changing is not a decision. Meta rewording a paragraph must not
silently alter what every review in flight is judged against, because nobody
reviewed the change and no version number moved. So refresh only ever adds a
snapshot; turning snapshots into the rules production uses is
:class:`~meobot.application.pr_policy_pack_service.PrPolicyPackService`'s job and
takes an explicit activation.

That separation is also what makes a failed refresh harmless: today's fetch
failing leaves yesterday's ACTIVE pack exactly where it was, and reviews keep
working.

Discovery is bounded on purpose
-------------------------------

A ``DISCOVERY_INDEX`` source yields links that start with its own
``discovery_prefix``, on its own approved host, one level deep. No recursion, no
sitemap, no search engine. Every discovered URL still goes through the fetcher's
allowlist, so the prefix is a second bound rather than the only one.

Discovered pages are **reported, not registered**. An operator adds the ones
worth ingesting to the registry; a crawler that registered whatever it found
would be a crawler, and would eventually ingest a marketing page.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import urljoin

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.core.logging import get_logger
from meobot.db.models.pr_platform_policy import (
    PrPlatformPolicySnapshot,
    PrPlatformPolicySource,
)
from meobot.domain.pr.models import (
    PrPolicyIngestionMethod,
    PrPolicySourceRole,
)
from meobot.domain.pr.policy_sources import POLICY_SOURCE_SEEDS
from meobot.integrations.platform_policy.allowlist import is_approved_url
from meobot.integrations.platform_policy.fetcher import (
    FetchedPolicyPage,
    PolicySourceFetcher,
    content_hash,
)
from meobot.integrations.platform_policy.normalizer import (
    MIN_USEFUL_CHARS,
    PARSER_VERSION,
    normalize_policy_text,
)

logger = get_logger(__name__)

#: Links one index may report. Bounded so a page that lists a hundred articles
#: cannot turn one refresh into a hundred fetches.
MAX_DISCOVERED_LINKS = 40

_HREF = re.compile(r"""(?is)<a\b[^>]*\bhref\s*=\s*["']([^"'#\s]+)["']""")


def _discovery_prefix_for(source: PrPlatformPolicySource) -> str | None:
    """The manifest's discovery prefix for one registered index, if it has one.

    Looked up by ``source_family`` rather than stored on the row: it is a
    property of the *manifest entry*, and adding a column to
    ``pr_platform_policy_sources`` for it would mean a migration for a value
    that never varies per deployment.
    """
    for seed in POLICY_SOURCE_SEEDS:
        if seed.source_family == source.source_family:
            return seed.discovery_prefix
    return None


@dataclass(frozen=True, slots=True)
class SourceRefreshOutcome:
    """What one source's refresh did."""

    source_id: uuid.UUID
    source_family: str
    changed: bool
    snapshot_id: uuid.UUID | None = None
    discovered_urls: tuple[str, ...] = ()
    error_code: str | None = None
    skipped_reason: str | None = None


class PrPolicySourceService:
    """Registry and snapshots. Never packs.

    Args:
        session: Unit of work; the caller owns the transaction boundary.
        fetcher: The one HTTP client allowed to read official pages. Optional so
            an operator-import-only flow, and every unit test, can construct
            this service without one.
    """

    def __init__(self, session: AsyncSession, fetcher: PolicySourceFetcher | None = None) -> None:
        self._session = session
        self._fetcher = fetcher

    # --- Registry ---------------------------------------------------------
    async def seed(self) -> int:
        """Insert any manifest source the database does not have yet.

        Idempotent, and non-destructive: an existing row is left alone even if
        the manifest's URL has since changed, because an operator may have
        corrected it in the database and that correction is the newer fact.
        Returns how many rows were added.
        """
        added = 0
        for seed in POLICY_SOURCE_SEEDS:
            existing = await self._session.execute(
                select(PrPlatformPolicySource).where(
                    PrPlatformPolicySource.platform_code == seed.platform_code,
                    PrPlatformPolicySource.policy_scope == seed.policy_scope,
                    PrPlatformPolicySource.source_family == seed.source_family,
                )
            )
            if existing.scalars().first() is not None:
                continue
            self._session.add(
                PrPlatformPolicySource(
                    platform_code=seed.platform_code,
                    policy_scope=seed.policy_scope,
                    source_role=seed.source_role,
                    source_family=seed.source_family,
                    name=seed.name,
                    canonical_url=seed.canonical_url,
                    ingestion_method=seed.ingestion_method,
                    enabled=True,
                )
            )
            added += 1
        await self._session.flush()
        logger.info("policy_sources_seeded", extra={"added": added})
        return added

    async def list_sources(
        self, *, platform_code: str | None = None, enabled_only: bool = False
    ) -> Sequence[PrPlatformPolicySource]:
        statement = select(PrPlatformPolicySource)
        if platform_code:
            statement = statement.where(PrPlatformPolicySource.platform_code == platform_code)
        if enabled_only:
            statement = statement.where(PrPlatformPolicySource.enabled.is_(True))
        result = await self._session.execute(
            statement.order_by(
                PrPlatformPolicySource.platform_code, PrPlatformPolicySource.source_family
            )
        )
        return result.scalars().all()

    async def source_by_family(self, source_family: str) -> PrPlatformPolicySource | None:
        result = await self._session.execute(
            select(PrPlatformPolicySource).where(
                PrPlatformPolicySource.source_family == source_family
            )
        )
        return result.scalars().first()

    # --- Snapshots --------------------------------------------------------
    async def latest_snapshot(self, source_id: uuid.UUID) -> PrPlatformPolicySnapshot | None:
        result = await self._session.execute(
            select(PrPlatformPolicySnapshot)
            .where(PrPlatformPolicySnapshot.source_id == source_id)
            .order_by(PrPlatformPolicySnapshot.fetched_at.desc())
            .limit(1)
        )
        return result.scalars().first()

    async def list_snapshots(
        self, *, source_id: uuid.UUID | None = None, limit: int = 50
    ) -> Sequence[PrPlatformPolicySnapshot]:
        statement = select(PrPlatformPolicySnapshot)
        if source_id is not None:
            statement = statement.where(PrPlatformPolicySnapshot.source_id == source_id)
        result = await self._session.execute(
            statement.order_by(PrPlatformPolicySnapshot.fetched_at.desc()).limit(limit)
        )
        return result.scalars().all()

    async def refresh_source(self, source: PrPlatformPolicySource) -> SourceRefreshOutcome:
        """Fetch one source and append a snapshot if its text changed.

        Never raises for an expected failure: a source that is down, refuses, or
        returns a page too thin to be policy is reported and the caller moves on.
        The ACTIVE pack is untouched in every branch.
        """
        logger.info(
            "policy_source_refresh_started",
            extra={"policy_source_family": source.source_family},
        )
        if self._fetcher is None or source.ingestion_method is not PrPolicyIngestionMethod.FETCH:
            # Operator-import sources are supplied by hand; there is nothing to
            # poll, and a refresh must not report that as a failure.
            return SourceRefreshOutcome(
                source_id=source.id,
                source_family=source.source_family,
                changed=False,
                skipped_reason="not_fetchable",
            )
        try:
            page = await self._fetcher.fetch(source.canonical_url)
        except Exception as exc:
            code = getattr(exc, "code", None) or type(exc).__name__
            logger.warning(
                "policy_source_refresh_failed",
                extra={"policy_source_family": source.source_family, "error_code": str(code)[:64]},
            )
            return SourceRefreshOutcome(
                source_id=source.id,
                source_family=source.source_family,
                changed=False,
                error_code=str(code)[:64],
            )

        discovered: tuple[str, ...] = ()
        if source.source_role is PrPolicySourceRole.DISCOVERY_INDEX:
            # An index contributes no rules; its whole job is to report
            # sub-pages an operator may then register.
            discovered = self._discover(page, source, _discovery_prefix_for(source))
            logger.info(
                "policy_source_discovered",
                extra={
                    "policy_source_family": source.source_family,
                    "discovered": len(discovered),
                },
            )
            return SourceRefreshOutcome(
                source_id=source.id,
                source_family=source.source_family,
                changed=False,
                discovered_urls=discovered,
                skipped_reason="discovery_index",
            )

        return await self._store(source, page)

    async def refresh_all(self) -> list[SourceRefreshOutcome]:
        """Refresh every enabled source. One failure does not stop the rest."""
        outcomes: list[SourceRefreshOutcome] = []
        for source in await self.list_sources(enabled_only=True):
            outcomes.append(await self.refresh_source(source))
        return outcomes

    async def import_snapshot(
        self, source: PrPlatformPolicySource, *, text: str, fetched_at: object = None
    ) -> SourceRefreshOutcome:
        """Store operator-supplied text as a snapshot, with the same handling.

        The fallback path. It hashes, deduplicates and records provenance
        exactly as a fetch does, and the snapshot says ``OPERATOR_IMPORT`` so an
        audit is never told the text was retrieved when it was pasted.
        """
        from meobot.core.time import utcnow

        normalized = normalize_policy_text(text)
        page = FetchedPolicyPage(
            final_url=source.canonical_url,
            fetched_at=fetched_at or utcnow(),  # type: ignore[arg-type]
            normalized_content=normalized,
            content_sha256=content_hash(normalized),
            parser_version=PARSER_VERSION,
        )
        return await self._store(source, page, method=PrPolicyIngestionMethod.OPERATOR_IMPORT)

    # --- Internals --------------------------------------------------------
    async def _store(
        self,
        source: PrPlatformPolicySource,
        page: FetchedPolicyPage,
        *,
        method: PrPolicyIngestionMethod | None = None,
    ) -> SourceRefreshOutcome:
        """Append a snapshot, unless the text is unchanged or too thin."""
        if page.length < MIN_USEFUL_CHARS:
            # A page that rendered client-side, or a challenge interstitial.
            # Storing it would build a pack out of a cookie banner.
            logger.warning(
                "policy_source_too_thin",
                extra={
                    "policy_source_family": source.source_family,
                    "normalized_chars": page.length,
                    "minimum": MIN_USEFUL_CHARS,
                },
            )
            return SourceRefreshOutcome(
                source_id=source.id,
                source_family=source.source_family,
                changed=False,
                error_code="content_too_thin",
            )

        existing = await self._session.execute(
            select(PrPlatformPolicySnapshot).where(
                PrPlatformPolicySnapshot.source_id == source.id,
                PrPlatformPolicySnapshot.content_sha256 == page.content_sha256,
            )
        )
        if existing.scalars().first() is not None:
            logger.info(
                "policy_source_unchanged",
                extra={
                    "policy_source_family": source.source_family,
                    "content_sha256": page.content_sha256,
                },
            )
            return SourceRefreshOutcome(
                source_id=source.id, source_family=source.source_family, changed=False
            )

        snapshot = PrPlatformPolicySnapshot(
            source_id=source.id,
            fetched_at=page.fetched_at,
            canonical_url=page.final_url,
            ingestion_method=method or source.ingestion_method,
            content_sha256=page.content_sha256,
            parser_version=page.parser_version,
            normalized_content=page.normalized_content,
        )
        self._session.add(snapshot)
        await self._session.flush()
        logger.info(
            "policy_source_changed",
            extra={
                "policy_source_family": source.source_family,
                "content_sha256": page.content_sha256,
                "normalized_chars": page.length,
                "policy_snapshot_id": str(snapshot.id),
            },
        )
        logger.info(
            "policy_snapshot_created",
            extra={
                "policy_snapshot_id": str(snapshot.id),
                "policy_source_family": source.source_family,
            },
        )
        return SourceRefreshOutcome(
            source_id=source.id,
            source_family=source.source_family,
            changed=True,
            snapshot_id=snapshot.id,
        )

    @staticmethod
    def _discover(
        page: FetchedPolicyPage,
        source: PrPlatformPolicySource,
        discovery_prefix: str | None = None,
    ) -> tuple[str, ...]:
        """Sub-page links under this index's own prefix. One level, no recursion.

        The prefix comes from the manifest and defaults to the source's own URL,
        so an index can only ever point deeper into itself. Every candidate is
        additionally checked against the fetcher's host allowlist, which is the
        bound that holds even if a prefix is later misconfigured.
        """
        # The prefix an index may descend into. Defaults to the index's own URL,
        # so a page can only ever point deeper into itself; a help centre whose
        # articles are siblings rather than children needs the shared parent
        # path, which is what the manifest's ``discovery_prefix`` supplies.
        seed_prefix = discovery_prefix or source.canonical_url
        found: list[str] = []
        seen: set[str] = set()
        # Read from the **markup**, not from ``normalized_content``: normalizing
        # strips every tag, so scanning it for anchors could only ever return
        # nothing. That is what it did, silently, until this was measured
        # against the real TikTok ads index - 50 anchors on the page, 0 found.
        for match in _HREF.findall(page.raw_markup):
            candidate = urljoin(page.final_url, match)
            if not candidate.startswith(seed_prefix) or candidate == seed_prefix:
                continue
            if not is_approved_url(candidate) or candidate in seen:
                continue
            seen.add(candidate)
            found.append(candidate)
            if len(found) >= MAX_DISCOVERED_LINKS:
                break
        return tuple(found)


__all__ = ["MAX_DISCOVERED_LINKS", "PrPolicySourceService", "SourceRefreshOutcome"]
