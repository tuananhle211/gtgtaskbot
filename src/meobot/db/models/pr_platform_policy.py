"""Official platform policy: where it came from, and which version reviewed what.

Step 1F.1. Four tables and one join, in a deliberate chain where each link is
immutable once written:

```
pr_platform_policy_sources     which official page, and how it is ingested
        │  (mutable registry - a URL can legitimately move)
        ▼
pr_platform_policy_snapshots   one immutable capture, content-hashed
        │
        ▼
pr_platform_policy_packs       one immutable version, ACTIVE at most once
        │      per (platform, distribution_mode)
        ▼
pr_platform_policy_rules       normalized rules, each pointing at its snapshot
        │
        ▼
pr_ai_review_run_policy_packs  which pack a given review actually used
```

Why the chain matters
---------------------

A finding stored today says "this content may breach rule ``TT-CG-03``". Two
years from now somebody has to be able to ask what ``TT-CG-03`` said, where it
came from, and on what date - after TikTok has rewritten the page twice. That is
only answerable if the rule, its snapshot and the pack the run pinned are all
still exactly as they were. So none of them is ever updated in place: a policy
change is a new snapshot, a new pack and a new version, and the old rows stay.

The registry at the top *is* mutable, because a canonical URL genuinely moves.
Nothing downstream depends on it: a snapshot records the URL it was actually
retrieved from, so moving the registry entry cannot rewrite history.

Provenance is not optional
--------------------------

``pr_platform_policy_rules.source_snapshot_id`` is ``NOT NULL``. There is no way
to store a rule that came from nowhere, which is the whole defence against a
pack quietly accumulating text somebody typed from memory - or that a model
invented while normalizing.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from meobot.db.base import Base, UUIDPrimaryKeyMixin, value_enum
from meobot.db.models.pr import RESTRICT, _not_empty
from meobot.domain.pr.models import (
    PrDistributionMode,
    PrPolicyIngestionMethod,
    PrPolicyPackStatus,
    PrPolicyScope,
    PrPolicySourceRole,
)

#: One ACTIVE pack per platform and distribution mode, enforced by a partial
#: unique index. Written out because an index cannot import Python.
_ACTIVE_PACK_PREDICATE = "status = 'ACTIVE'"


class PrPlatformPolicySource(Base, UUIDPrimaryKeyMixin):
    """One official policy page MeoBot ingests.

    Data, not code: a canonical URL moves, and chasing it through a service
    module would mean a deploy every time a platform reorganises its help
    centre. The registry is seeded by an ops command and edited in the database.

    ``ingestion_method`` says how the text arrives. ``FETCH`` is the ordinary
    case. ``OPERATOR_IMPORT`` exists because some official pages render their
    policy client-side and return no prose to an HTTP client - see the module
    docstring of :mod:`meobot.integrations.platform_policy.fetcher`. Falling back
    to an unofficial mirror was the alternative, and it is not one.
    """

    __tablename__ = "pr_platform_policy_sources"
    __table_args__ = (
        CheckConstraint(_not_empty("platform_code"), name="platform_code_set"),
        CheckConstraint(_not_empty("name"), name="name_not_empty"),
        # Only ever https, and only ever a host the fetcher allowlists. The
        # column check is the cheap half; the allowlist is the real one.
        CheckConstraint("canonical_url LIKE 'https://%'", name="url_is_https"),
        Index(
            "uq_pr_platform_policy_sources_family",
            "platform_code",
            "policy_scope",
            "source_family",
            unique=True,
        ),
        Index("ix_pr_platform_policy_sources_enabled", "enabled", "platform_code"),
    )

    #: Matches ``pr_platforms.code``. A code rather than a foreign key: the
    #: registry is seeded before a deployment necessarily has platform rows,
    #: and the policy family is a fact about Facebook rather than about one
    #: installation's row for it.
    platform_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    policy_scope: Mapped[PrPolicyScope] = mapped_column(
        value_enum(PrPolicyScope, name="pr_policy_scope", length=30), nullable=False
    )
    #: Whether this page contributes rules or only discovers sub-pages. An
    #: index that contributed rules would ground reviews in a table of
    #: contents - see :class:`PrPolicySourceRole`.
    source_role: Mapped[PrPolicySourceRole] = mapped_column(
        value_enum(PrPolicySourceRole, name="pr_policy_source_role", length=20),
        nullable=False,
        default=PrPolicySourceRole.POLICY_CONTENT,
        server_default=PrPolicySourceRole.POLICY_CONTENT.value,
    )
    #: Stable identifier used by ops commands, e.g. ``META_COMMUNITY_STANDARDS``.
    source_family: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    ingestion_method: Mapped[PrPolicyIngestionMethod] = mapped_column(
        value_enum(PrPolicyIngestionMethod, name="pr_policy_ingestion_method", length=20),
        nullable=False,
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class PrPlatformPolicySnapshot(Base, UUIDPrimaryKeyMixin):
    """One immutable capture of one source's text.

    Append-only, like every other audit record in this schema. No ``updated_at``
    and nothing updates a row.

    ``content_sha256`` is unique per source, which is the deduplication rule:
    re-reading an unchanged page produces the same hash and no new row, so the
    daily refresh does not accumulate a snapshot a day for a page nobody edited.
    A genuine edit produces a different hash and a new snapshot, which is what
    "the policy changed on this date" means here.

    ``normalized_content`` is the stripped policy text - headings and prose. Not
    raw HTML: no scripts, no navigation, no analytics blobs, no cookies and no
    request headers ever reach this column.
    """

    __tablename__ = "pr_platform_policy_snapshots"
    __table_args__ = (
        CheckConstraint("length(content_sha256) = 64", name="sha256_length"),
        CheckConstraint(_not_empty("normalized_content"), name="content_not_empty"),
        CheckConstraint(_not_empty("parser_version"), name="parser_version_set"),
        # The deduplication rule, in the database.
        Index(
            "uq_pr_platform_policy_snapshots_source_hash",
            "source_id",
            "content_sha256",
            unique=True,
        ),
        Index("ix_pr_platform_policy_snapshots_source_fetched", "source_id", "fetched_at"),
    )

    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "pr_platform_policy_sources.id", ondelete=RESTRICT, name="fk_policy_snapshot_source"
        ),
        nullable=False,
    )
    #: When MeoBot captured it. Not when the platform last edited the page.
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: The platform's own "last updated" where the page states one. Usually null.
    source_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: The URL actually retrieved, after redirects. May differ from the
    #: registry's canonical URL, which is why it is recorded per snapshot.
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    ingestion_method: Mapped[PrPolicyIngestionMethod] = mapped_column(
        value_enum(PrPolicyIngestionMethod, name="pr_policy_ingestion_method", length=20),
        nullable=False,
    )
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    parser_version: Mapped[str] = mapped_column(String(64), nullable=False)
    normalized_content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PrPlatformPolicyPack(Base, UUIDPrimaryKeyMixin):
    """One versioned, immutable set of rules for a platform and mode.

    Immutable **once ACTIVE**. A ``DRAFT`` may still be assembled; the moment it
    is activated it becomes the thing stored findings cite, and editing it would
    make an old review unexplainable. A policy update is a new version.

    ``uq_pr_platform_policy_packs_active`` is a partial unique index over
    ``(platform_code, distribution_mode)`` where the status is ``ACTIVE``, so two
    active packs for TikTok paid ads cannot exist however the activation raced.
    """

    __tablename__ = "pr_platform_policy_packs"
    __table_args__ = (
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(_not_empty("platform_code"), name="platform_code_set"),
        CheckConstraint(_not_empty("label"), name="label_not_empty"),
        CheckConstraint("length(manifest_hash) = 64", name="manifest_hash_length"),
        # A retired pack was active once; an active one has not been retired.
        CheckConstraint(
            "activated_at IS NOT NULL OR retired_at IS NULL", name="retired_needs_active"
        ),
        # Distribution mode composes the pack, so UNSPECIFIED cannot have one.
        CheckConstraint("distribution_mode IN ('ORGANIC', 'PAID_AD')", name="mode_is_specified"),
        Index(
            "uq_pr_platform_policy_packs_active",
            "platform_code",
            "distribution_mode",
            unique=True,
            postgresql_where=text(_ACTIVE_PACK_PREDICATE),
            sqlite_where=text(_ACTIVE_PACK_PREDICATE),
        ),
        Index(
            "uq_pr_platform_policy_packs_version",
            "platform_code",
            "distribution_mode",
            "version",
            unique=True,
        ),
    )

    platform_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    distribution_mode: Mapped[PrDistributionMode] = mapped_column(
        value_enum(PrDistributionMode, name="pr_distribution_mode", length=20), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    #: What a person sees, e.g. ``TIKTOK-PAID_AD-2026-08-09.1``. Generated, and
    #: stored rather than recomputed so it cannot drift from the row.
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[PrPolicyPackStatus] = mapped_column(
        value_enum(PrPolicyPackStatus, name="pr_policy_pack_status", length=20),
        nullable=False,
        index=True,
    )
    #: Over the ordered rule ids and their text. Two packs with the same hash
    #: contain the same rules, which is how "did anything actually change" is
    #: answered without diffing prose.
    manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete=RESTRICT, name="fk_policy_pack_created_by"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class PrPlatformPolicyRule(Base, UUIDPrimaryKeyMixin):
    """One normalized rule inside one pack, with its official provenance.

    ``rule_id`` is the stable string a model may cite - ``TT-CG-03`` - and it is
    unique per pack. A finding that names an id this table does not have for the
    pinned pack is a hallucinated citation and is rejected before anything is
    stored.

    ``source_snapshot_id`` is ``NOT NULL`` on purpose. There is no way to add a
    rule with no official source behind it.
    """

    __tablename__ = "pr_platform_policy_rules"
    __table_args__ = (
        CheckConstraint(_not_empty("rule_id"), name="rule_id_not_empty"),
        CheckConstraint(_not_empty("title"), name="title_not_empty"),
        CheckConstraint(_not_empty("rule_text"), name="rule_text_not_empty"),
        Index("uq_pr_platform_policy_rules_pack_rule", "pack_id", "rule_id", unique=True),
        Index("ix_pr_platform_policy_rules_pack", "pack_id"),
    )

    pack_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pr_platform_policy_packs.id", ondelete=RESTRICT, name="fk_policy_rule_pack"),
        nullable=False,
    )
    #: Stable within the pack, and what a finding cites.
    rule_id: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    policy_scope: Mapped[PrPolicyScope] = mapped_column(
        value_enum(PrPolicyScope, name="pr_policy_scope", length=30), nullable=False
    )
    rule_text: Mapped[str] = mapped_column(Text, nullable=False)
    #: Never null. A rule with no snapshot behind it cannot be stored.
    source_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "pr_platform_policy_snapshots.id",
            ondelete=RESTRICT,
            name="fk_policy_rule_snapshot",
        ),
        nullable=False,
    )
    #: Denormalized from the snapshot so a citation renders without a join, and
    #: so it keeps saying what it said if the registry's URL later moves.
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    #: Where in the document, e.g. ``Community Guidelines > Integrity``.
    section_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PrAiReviewRunPolicyPack(Base, UUIDPrimaryKeyMixin):
    """Which pack one review run pinned, for which target context.

    The row that makes a two-year-old finding explainable. Written when the run
    is queued - **not** read when the worker starts - so a pack activated in
    between cannot change what an in-flight review was judged against.

    One run may pin several: a piece targeting Facebook organically and TikTok
    as a paid ad pins two packs, and a finding says which platform it concerns.
    """

    __tablename__ = "pr_ai_review_run_policy_packs"
    __table_args__ = (
        # One pack per run per platform/mode context. A content item with two
        # Facebook organic targets pins the Facebook organic pack once.
        Index(
            "uq_pr_ai_review_run_policy_packs_context",
            "run_id",
            "platform_code",
            "distribution_mode",
            unique=True,
        ),
        Index("ix_pr_ai_review_run_policy_packs_run", "run_id"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pr_ai_review_runs.id", ondelete=RESTRICT, name="fk_run_policy_pack_run"),
        nullable=False,
    )
    policy_pack_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "pr_platform_policy_packs.id", ondelete=RESTRICT, name="fk_run_policy_pack_pack"
        ),
        nullable=False,
    )
    platform_code: Mapped[str] = mapped_column(String(64), nullable=False)
    distribution_mode: Mapped[PrDistributionMode] = mapped_column(
        value_enum(PrDistributionMode, name="pr_distribution_mode", length=20), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


__all__: list[str] = [
    "PrAiReviewRunPolicyPack",
    "PrPlatformPolicyPack",
    "PrPlatformPolicyRule",
    "PrPlatformPolicySnapshot",
    "PrPlatformPolicySource",
]
