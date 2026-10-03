"""Building, activating and resolving versioned policy packs.

Step 1F.1. A pack is what a review is actually judged against, so this service
owns the one moment production policy changes - and makes it an explicit,
atomic, operator-driven act rather than a consequence of a website edit.

```
snapshots ─→ build_draft()  DRAFT, versioned, rules with NOT NULL provenance
                 │
                 ▼
             activate()     previous ACTIVE → RETIRED, this one → ACTIVE
                 │          (one transaction, one partial unique index)
                 ▼
          resolve_active()  what a new run pins. Never what a worker looks up.
```

Immutability
------------

Once a pack is ``ACTIVE`` nothing in this service edits it, its rules or their
snapshot links. :meth:`build_draft` refuses to touch a non-``DRAFT`` pack, and
the composition step only ever inserts rows for a pack it just created. A policy
update is a new version; the old one is retired and kept, because a review
recorded last year cites it and has to stay explainable.

Composition
-----------

``PACK_SCOPES`` decides which scopes compose which mode: organic content is held
to the community standards, a paid ad to those *and* the advertising standards.
Which snapshots supply those scopes is a database question - the newest snapshot
per enabled ``POLICY_CONTENT`` source in scope - so adding a source family
changes the next pack without changing this file.

An index contributes nothing. ``PrPolicySourceRole.DISCOVERY_INDEX`` sources are
excluded from composition entirely, which is what stops a pack being built out
of a table of contents.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.core.errors import MeoBotError
from meobot.core.logging import get_logger
from meobot.core.time import utcnow
from meobot.db.models.pr_platform_policy import (
    PrPlatformPolicyPack,
    PrPlatformPolicyRule,
    PrPlatformPolicySnapshot,
    PrPlatformPolicySource,
)
from meobot.domain.pr.ai_review import PolicyContext, PolicyRuleRef
from meobot.domain.pr.models import (
    PACK_SCOPES,
    PrDistributionMode,
    PrPolicyPackStatus,
    PrPolicyScope,
    PrPolicySourceRole,
)
from meobot.integrations.platform_policy.normalizer import chunk_text, split_sections

logger = get_logger(__name__)

#: A hard ceiling per source, set far above any real policy page. It exists so
#: a parser bug that produced a hundred thousand fragments cannot fill the
#: database, **not** to bound the prompt - the prompt's budget is
#: :mod:`meobot.application.pr_policy_selector`'s job, at review time.
#:
#: The previous value was 24, applied to the *longest* sections. That silently
#: deleted policy: TikTok's guidelines extract to hundreds of sections, and a
#: one-line prohibition ("no ads for prescription drugs") lost its place to a
#: three-paragraph explanation of why the platform cares about safety. Length is
#: not importance, and a pack is the auditable record - it must be complete.
MAX_RULES_PER_SOURCE = 2000

#: Below this a "section" is a heading with no body - a nav remnant rather than
#: a rule, and not something a finding should be able to cite. Applied to whole
#: **sections**, never to a chunk: dropping a short tail of a real section would
#: lose policy text, which is the thing this module must not do.
MIN_RULE_CHARS = 120

#: ``pr_platform_policy_rules.rule_id`` is ``VARCHAR(64)``. Ids are built to
#: fit, and a family long enough to threaten it is shortened and given a
#: deterministic digest rather than being allowed to truncate into a collision.
MAX_RULE_ID_CHARS = 64

#: The fixed tail every id carries: ``-<section>-<chunk>``.
_ID_SUFFIX_TEMPLATE = "-000-00"

#: Short, stable platform tokens. A code with no entry falls back to its first
#: two letters, which is enough because the family follows it.
_PLATFORM_TOKENS = {"FACEBOOK": "FB", "TIKTOK": "TT"}

#: How large one stored rule may be. A bound for the database row and for prompt
#: composition - **not** a truncation point. A section over this is *split* into
#: as many complete rules as it needs, so every character stays in the pack.
#:
#: The previous implementation wrote ``section.text[:8000]``, which turned
#: 685,226 characters of TikTok's Community Guidelines into one 8,000-character
#: rule and silently discarded 98.8% of the policy. A pack that has thrown away
#: the policy is not an auditable record of it.
MAX_RULE_TEXT_CHARS = 6_000


class PrPolicyPackError(MeoBotError):
    """A pack could not be built or activated."""

    code = "policy_pack_error"


class PrPolicyPackService:
    """Owns the pack lifecycle. The only writer of ``pr_platform_policy_packs``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- Building ---------------------------------------------------------
    async def build_draft(
        self,
        *,
        platform_code: str,
        distribution_mode: PrDistributionMode,
        created_by_user_id: uuid.UUID | None = None,
    ) -> PrPlatformPolicyPack:
        """Compose a new ``DRAFT`` from the newest substantive snapshots.

        Raises:
            PrPolicyPackError: The mode composes no scopes, a required scope has
                no usable snapshot, or the result would contain no rules. A pack
                with no rules would activate happily and ground every review in
                nothing, so it is refused at the point it is built.
        """
        scopes = PACK_SCOPES.get(distribution_mode)
        if not scopes:
            raise PrPolicyPackError(
                "Chỉ dựng được policy pack cho Organic hoặc Quảng cáo trả phí.",
                details={"distribution_mode": distribution_mode.value},
            )

        rules: list[dict[str, object]] = []
        for scope in scopes:
            snapshots = await self._newest_content_snapshots(platform_code, scope)
            if not snapshots:
                raise PrPolicyPackError(
                    "Chưa có bản chụp chính sách nào cho phạm vi này.",
                    details={
                        "reason": "no_snapshot_for_scope",
                        "platform_code": platform_code,
                        "policy_scope": scope.value,
                    },
                )
            for source, snapshot in snapshots:
                # Numbering is **source-local**: a rule id carries its own
                # source, so sections restart at 001 for each one and no
                # cross-source bookkeeping can get it wrong.
                rules.extend(self._rules_from(source, snapshot, scope))

        if not rules:
            raise PrPolicyPackError(
                "Không trích được điều khoản nào từ các bản chụp hiện có.",
                details={"reason": "no_rules_extracted", "platform_code": platform_code},
            )

        version = await self._next_version(platform_code, distribution_mode)
        today = utcnow().date().isoformat()
        label = f"{platform_code}-{distribution_mode.value}-{today}.{version}"
        pack = PrPlatformPolicyPack(
            platform_code=platform_code,
            distribution_mode=distribution_mode,
            version=version,
            label=label,
            status=PrPolicyPackStatus.DRAFT,
            manifest_hash=_manifest_hash(rules),
            created_by_user_id=created_by_user_id,
        )
        self._session.add(pack)
        await self._session.flush()

        for rule in rules:
            self._session.add(PrPlatformPolicyRule(pack_id=pack.id, **rule))
        await self._session.flush()

        logger.info(
            "policy_pack_created",
            extra={
                "policy_pack_id": str(pack.id),
                "policy_pack_label": pack.label,
                "rules": len(rules),
                "manifest_hash": pack.manifest_hash,
            },
        )
        return pack

    # --- Activation -------------------------------------------------------
    async def activate(self, pack: PrPlatformPolicyPack) -> PrPlatformPolicyPack:
        """Make one draft the pack new reviews use. Atomic.

        The previous active pack is retired in the same transaction, so there is
        never a moment with two - and the partial unique index refuses the write
        if a concurrent activation got there first, rather than leaving the
        database with two packs claiming to be current.

        Existing runs are untouched: they pinned a pack id when they were
        queued, and this changes nothing about that.
        """
        if pack.status is not PrPolicyPackStatus.DRAFT:
            raise PrPolicyPackError(
                "Chỉ kích hoạt được policy pack đang ở trạng thái DRAFT.",
                details={"policy_pack_id": str(pack.id), "status": pack.status.value},
            )

        moment = utcnow()
        current = await self.resolve_active_pack(pack.platform_code, pack.distribution_mode)
        if current is not None:
            current.status = PrPolicyPackStatus.RETIRED
            current.retired_at = moment
            logger.info(
                "policy_pack_retired",
                extra={"policy_pack_id": str(current.id), "policy_pack_label": current.label},
            )
        # Flushed before the new row so the partial unique index sees the old
        # one leave ACTIVE first.
        await self._session.flush()

        pack.status = PrPolicyPackStatus.ACTIVE
        pack.activated_at = moment
        await self._session.flush()
        logger.info(
            "policy_pack_activated",
            extra={
                "policy_pack_id": str(pack.id),
                "policy_pack_label": pack.label,
                "platform_code": pack.platform_code,
                "distribution_mode": pack.distribution_mode.value,
                "retired": str(current.id) if current else None,
            },
        )
        return pack

    # --- Reading ----------------------------------------------------------
    async def resolve_active_pack(
        self, platform_code: str, distribution_mode: PrDistributionMode
    ) -> PrPlatformPolicyPack | None:
        """The pack a *new* run would pin. Never used to reinterpret an old one."""
        result = await self._session.execute(
            select(PrPlatformPolicyPack).where(
                PrPlatformPolicyPack.platform_code == platform_code,
                PrPlatformPolicyPack.distribution_mode == distribution_mode,
                PrPlatformPolicyPack.status == PrPolicyPackStatus.ACTIVE,
            )
        )
        return result.scalars().first()

    async def get_pack(self, pack_id: uuid.UUID) -> PrPlatformPolicyPack | None:
        return await self._session.get(PrPlatformPolicyPack, pack_id)

    async def list_packs(
        self, *, platform_code: str | None = None
    ) -> Sequence[PrPlatformPolicyPack]:
        statement = select(PrPlatformPolicyPack)
        if platform_code:
            statement = statement.where(PrPlatformPolicyPack.platform_code == platform_code)
        result = await self._session.execute(
            statement.order_by(
                PrPlatformPolicyPack.platform_code,
                PrPlatformPolicyPack.distribution_mode,
                PrPlatformPolicyPack.version.desc(),
            )
        )
        return result.scalars().all()

    async def rules_for(self, pack_id: uuid.UUID) -> Sequence[PrPlatformPolicyRule]:
        result = await self._session.execute(
            select(PrPlatformPolicyRule)
            .where(PrPlatformPolicyRule.pack_id == pack_id)
            .order_by(PrPlatformPolicyRule.rule_id)
        )
        return result.scalars().all()

    async def context_for(self, pack: PrPlatformPolicyPack) -> PolicyContext:
        """One pinned pack, in the shape the prompt and the validator use."""
        rules = await self.rules_for(pack.id)
        return PolicyContext(
            platform_code=pack.platform_code,
            distribution_mode=pack.distribution_mode.value,
            pack_label=pack.label,
            pack_version=pack.version,
            rules=tuple(
                PolicyRuleRef(
                    rule_id=rule.rule_id,
                    title=rule.title,
                    text=rule.rule_text,
                    source_url=rule.source_url,
                    section_path=rule.section_path,
                )
                for rule in rules
            ),
        )

    # --- Internals --------------------------------------------------------
    async def _newest_content_snapshots(
        self, platform_code: str, scope: PrPolicyScope
    ) -> list[tuple[PrPlatformPolicySource, PrPlatformPolicySnapshot]]:
        """The latest snapshot of every enabled *content* source in one scope.

        Discovery indexes are excluded here, which is the single line that stops
        a table of contents from becoming production policy.
        """
        sources = await self._session.execute(
            select(PrPlatformPolicySource).where(
                PrPlatformPolicySource.platform_code == platform_code,
                PrPlatformPolicySource.policy_scope == scope,
                PrPlatformPolicySource.enabled.is_(True),
                PrPlatformPolicySource.source_role == PrPolicySourceRole.POLICY_CONTENT,
            )
        )
        pairs: list[tuple[PrPlatformPolicySource, PrPlatformPolicySnapshot]] = []
        for source in sources.scalars().all():
            newest = await self._session.execute(
                select(PrPlatformPolicySnapshot)
                .where(PrPlatformPolicySnapshot.source_id == source.id)
                .order_by(PrPlatformPolicySnapshot.fetched_at.desc())
                .limit(1)
            )
            snapshot = newest.scalars().first()
            if snapshot is not None:
                pairs.append((source, snapshot))
        return pairs

    @staticmethod
    def _rules_from(
        source: PrPlatformPolicySource,
        snapshot: PrPlatformPolicySnapshot,
        scope: PrPolicyScope,
    ) -> list[dict[str, object]]:
        """Split one snapshot into citable rules, deterministically.

        No model is involved. Every section the normalizer found that clears
        :data:`MIN_RULE_CHARS` becomes a rule, **in document order**, and every
        one carries the snapshot id and URL it came from - which is what makes
        ``source_snapshot_id`` satisfiable at all.

        Document order, not longest-first. The pack is the durable, auditable
        record of what the official page said; ordering it by length was a way
        of deciding what to throw away, and a pack should not be throwing
        anything away. Bounding what a *prompt* sees is a separate concern with
        its own module and its own version - see
        :mod:`meobot.application.pr_policy_selector`.

        A section larger than :data:`MAX_RULE_TEXT_CHARS` becomes **several
        complete rules** rather than one truncated one. That matters more than
        it sounds: these pages rarely carry the headings the splitter looks for -
        TikTok's guidelines extract to a single 685k section - so without
        chunking almost every source contributed exactly one rule of exactly
        8,000 characters, whatever its real size.

        Rule ids are ``<platform>-<scope>-<source family>-<section>-<chunk>``,
        numbered **within the source**. The source family is what makes them
        collision-free: ``uq_pr_platform_policy_sources_family`` is unique over
        ``(platform_code, policy_scope, source_family)``, and the scope is
        already in the id, so two sources in one pack cannot produce the same
        prefix however their sections happen to number.

        The previous scheme numbered sections pack-globally and advanced the
        offset by the count of *distinct section paths*. Real policy pages
        repeat headings - four sections called "Overview" collapse to one - so
        the offset under-counted, the next source started too low, and its ids
        overlapped the previous source's. Twenty TikTok advertising sources in
        one pack made that a certainty rather than a risk.

        Source-local numbering also means adding a source renames nothing:
        every other source's ids are computed from its own document.
        """
        sections = [
            section
            for section in split_sections(snapshot.normalized_content, root=source.name)
            if len(section.text) >= MIN_RULE_CHARS
        ]
        prefix = _rule_prefix(source.platform_code, scope, source.source_family)
        rules: list[dict[str, object]] = []
        for section_index, section in enumerate(sections, start=1):
            for chunk_index, chunk in enumerate(
                chunk_text(section.text, max_chars=MAX_RULE_TEXT_CHARS), start=1
            ):
                if len(rules) >= MAX_RULES_PER_SOURCE:
                    # A runaway-parser backstop, far above any real page. If it
                    # ever fires the pack is wrong and somebody should know.
                    logger.warning(
                        "policy_rules_capped",
                        extra={
                            "policy_source_family": source.source_family,
                            "cap": MAX_RULES_PER_SOURCE,
                        },
                    )
                    return rules
                rules.append(
                    {
                        "rule_id": f"{prefix}-{section_index:03d}-{chunk_index:02d}",
                        "title": section.heading[:300],
                        "policy_scope": scope,
                        "rule_text": chunk,
                        "source_snapshot_id": snapshot.id,
                        "source_url": snapshot.canonical_url,
                        "section_path": section.path,
                    }
                )
        return rules

    async def _next_version(self, platform_code: str, distribution_mode: PrDistributionMode) -> int:
        result = await self._session.execute(
            select(PrPlatformPolicyPack.version)
            .where(
                PrPlatformPolicyPack.platform_code == platform_code,
                PrPlatformPolicyPack.distribution_mode == distribution_mode,
            )
            .order_by(PrPlatformPolicyPack.version.desc())
            .limit(1)
        )
        return (result.scalars().first() or 0) + 1


def _rule_prefix(platform_code: str, scope: PrPolicyScope, source_family: str) -> str:
    """``TT-AD-HEALTHCARE`` - readable in a finding, and unique within a pack.

    The source family carries the uniqueness. It is unique per platform and
    scope by database constraint, and the scope is in the prefix, so two sources
    composed into one pack always differ here - which is what stops their
    section numbering from colliding.

    The **whole** family is used, not a shortened form. Stripping a redundant
    ``TIKTOK_ADS_`` read better but was not injective: ``META_CS_TEST`` and a
    family literally named ``TEST`` would both shorten to ``TEST`` and collide in
    one scope. Uniqueness here has to be provable rather than probable, and the
    database already guarantees it for the full family - so that is what the id
    carries, redundancy and all.
    """
    platform = _PLATFORM_TOKENS.get(platform_code, platform_code[:2].upper())
    kind = "AD" if scope is PrPolicyScope.ADVERTISING_STANDARDS else "CS"
    token = _source_token(source_family)
    prefix = f"{platform}-{kind}-{token}"
    if len(prefix) + len(_ID_SUFFIX_TEMPLATE) <= MAX_RULE_ID_CHARS:
        return prefix
    # A family long enough to threaten the column: keep a readable head and
    # settle uniqueness with a deterministic digest of the whole thing.
    room = MAX_RULE_ID_CHARS - len(_ID_SUFFIX_TEMPLATE) - len(platform) - len(kind) - 3 - 7
    digest = hashlib.sha256(source_family.encode("utf-8")).hexdigest()[:6].upper()
    return f"{platform}-{kind}-{token[: max(1, room)]}-{digest}"


def _source_token(source_family: str) -> str:
    """A source family, as it appears inside a rule id.

    Uppercased and reduced to characters that read cleanly in a citation.
    Deliberately **not** shortened: see :func:`_rule_prefix` on why an injective
    transformation matters more here than a tidy one.
    """
    return re.sub(r"[^A-Z0-9_]+", "", source_family.upper()) or "SRC"


def _manifest_hash(rules: Sequence[dict[str, object]]) -> str:
    """Over the ordered rule ids and text. Two packs that hash alike are alike."""
    digest = hashlib.sha256()
    for rule in rules:
        digest.update(str(rule["rule_id"]).encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(rule["rule_text"]).encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


__all__ = [
    "MAX_RULES_PER_SOURCE",
    "MAX_RULE_ID_CHARS",
    "MAX_RULE_TEXT_CHARS",
    "MIN_RULE_CHARS",
    "PrPolicyPackError",
    "PrPolicyPackService",
]
