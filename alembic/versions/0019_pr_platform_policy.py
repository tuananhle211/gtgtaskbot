"""Step 1F.1: versioned official platform policy, and organic vs paid targets.

Five new tables and **one added column**. No existing column changes type, and
no existing constraint is altered; revisions 0001-0018 are left as they are.

The added column
----------------

``pr_content_targets.distribution_mode``, ``NOT NULL DEFAULT 'UNSPECIFIED'``.

Every existing row becomes ``UNSPECIFIED``, and that is the whole point:
classifying them ``ORGANIC`` would have been one ``UPDATE`` and a silent lie.
A piece already running as a paid ad would then be reviewed against community
standards alone - exactly the mistake this column exists to prevent. For
Facebook and TikTok targets, ``UNSPECIFIED`` blocks AI review until a person
says which it is; nothing is guessed on their behalf.

The chain
---------

```
pr_platform_policy_sources     registry: which official page, ingested how
pr_platform_policy_snapshots   immutable capture, content-hashed, deduplicated
pr_platform_policy_packs       immutable version, ACTIVE at most once per
                               (platform, distribution_mode)
pr_platform_policy_rules       normalized rules, each with NOT NULL provenance
pr_ai_review_run_policy_packs  which pack a given review actually used
```

Two partial/unique indexes carry the guarantees:

* ``uq_pr_platform_policy_snapshots_source_hash`` - re-reading an unchanged page
  creates no row, so a daily refresh does not accumulate a snapshot a day for a
  page nobody edited;
* ``uq_pr_platform_policy_packs_active`` - partial, ``WHERE status = 'ACTIVE'``,
  so two active packs for one platform and mode cannot exist however an
  activation raced. Retired and draft rows are excluded, so version history
  accumulates normally.

**No network access happens here.** A migration that fetched a policy page would
be a migration that fails when a website is down, and would bake today's policy
text into a schema change. Ingestion is an ops command and a Celery beat job;
this revision creates empty tables.

Identifier lengths
------------------

PostgreSQL truncates identifiers at 63 bytes, and SQLAlchemy raises
``IdentifierError`` rather than truncating silently - which is what it did on
the first deployment attempt of this revision.

The cause is structural rather than one unlucky name: the ``fk`` naming
convention is ``fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s``,
and these table names are 24-29 characters each, so any generated foreign-key
name concatenating two of them runs to 68-75. Three did.

So every foreign key here carries an **explicit short name** (``fk_policy_rule_snapshot``
rather than ``fk_pr_platform_policy_rules_source_snapshot_id_pr_platform_policy_snapshots``),
and the longest check names were shortened too. The ceiling after this revision
is 50 characters, leaving 13 bytes of margin; ``tests/unit/test_pr_policy_grounded_review.py``
asserts it from the live metadata rather than from this file, so a future column
cannot reintroduce the failure unnoticed.

Delete behaviour
----------------

Every foreign key is ``ON DELETE RESTRICT``. Deleting a snapshot a rule cites,
or a pack a review pinned, fails rather than quietly destroying the provenance
that makes a stored finding explainable.

Downgrade
---------

Drops the five tables and the column. **Every distribution mode an operator set
is lost** - the column is where it lives - so re-upgrading leaves every target
``UNSPECIFIED`` again and needing to be set. Policy snapshots and packs are lost
too, which means they must be re-ingested and re-activated; the official pages
are unchanged, so that is a re-run of the ops commands rather than lost history.
Completed AI reviews survive: they live in ``pr_ai_reviews``, which this
revision does not touch. They simply stop being able to name the pack they used.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

USERS = "users"
AI_REVIEW_RUNS = "pr_ai_review_runs"
CONTENT_TARGETS = "pr_content_targets"

RESTRICT = "RESTRICT"

# --- The stored vocabulary --------------------------------------------------
# Literals rather than imports from ``meobot.domain.pr.models``: a migration has
# to keep meaning what it meant on the day it ran. A unit test asserts each of
# these still matches its enum.
DISTRIBUTION_MODES = ("UNSPECIFIED", "ORGANIC", "PAID_AD")
POLICY_SCOPES = ("COMMUNITY_STANDARDS", "ADVERTISING_STANDARDS")
PACK_STATUSES = ("DRAFT", "ACTIVE", "RETIRED")
INGESTION_METHODS = ("FETCH", "OPERATOR_IMPORT")
SOURCE_ROLES = ("POLICY_CONTENT", "DISCOVERY_INDEX")

#: The predicate behind the one-active-pack rule.
ACTIVE_PACK_PREDICATE = "status = 'ACTIVE'"


def _distribution_mode() -> sa.Enum:
    return sa.Enum(
        *DISTRIBUTION_MODES, name="pr_distribution_mode", native_enum=False, length=20
    )


def _policy_scope() -> sa.Enum:
    return sa.Enum(*POLICY_SCOPES, name="pr_policy_scope", native_enum=False, length=30)


def _source_role() -> sa.Enum:
    return sa.Enum(*SOURCE_ROLES, name="pr_policy_source_role", native_enum=False, length=20)


def _ingestion_method() -> sa.Enum:
    return sa.Enum(
        *INGESTION_METHODS, name="pr_policy_ingestion_method", native_enum=False, length=20
    )


def upgrade() -> None:
    # --- The one added column ------------------------------------------------
    # Server default so the backfill is the default rather than an UPDATE, and
    # so a row inserted by an older application version is still valid.
    op.add_column(
        CONTENT_TARGETS,
        sa.Column(
            "distribution_mode",
            _distribution_mode(),
            nullable=False,
            server_default="UNSPECIFIED",
        ),
    )

    # --- Registry ------------------------------------------------------------
    op.create_table(
        "pr_platform_policy_sources",
        sa.Column("platform_code", sa.String(length=64), nullable=False),
        sa.Column("policy_scope", _policy_scope(), nullable=False),
        # An index contributes no rules - see PrPolicySourceRole. Registering a
        # table of contents as policy content would ground reviews in
        # navigation, which is a pack that exists and means nothing.
        sa.Column(
            "source_role", _source_role(), nullable=False, server_default="POLICY_CONTENT"
        ),
        sa.Column("source_family", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("ingestion_method", _ingestion_method(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_pr_platform_policy_sources"),
        # Bare names - NAMING_CONVENTION adds the ``ck_<table>_`` prefix.
        sa.CheckConstraint("length(trim(platform_code)) > 0", name="platform_code_set"),
        sa.CheckConstraint("length(trim(name)) > 0", name="name_not_empty"),
        sa.CheckConstraint("canonical_url LIKE 'https://%'", name="url_is_https"),
    )
    op.create_index(
        "ix_pr_platform_policy_sources_platform_code",
        "pr_platform_policy_sources",
        ["platform_code"],
    )
    op.create_index(
        "ix_pr_platform_policy_sources_enabled",
        "pr_platform_policy_sources",
        ["enabled", "platform_code"],
    )
    op.create_index(
        "uq_pr_platform_policy_sources_family",
        "pr_platform_policy_sources",
        ["platform_code", "policy_scope", "source_family"],
        unique=True,
    )

    # --- Immutable snapshots -------------------------------------------------
    op.create_table(
        "pr_platform_policy_snapshots",
        sa.Column("source_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("ingestion_method", _ingestion_method(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("parser_version", sa.String(length=64), nullable=False),
        sa.Column("normalized_content", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_pr_platform_policy_snapshots"),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["pr_platform_policy_sources.id"],
            name="fk_policy_snapshot_source",
            ondelete=RESTRICT,
        ),
        sa.CheckConstraint("length(content_sha256) = 64", name="sha256_length"),
        sa.CheckConstraint(
            "length(trim(normalized_content)) > 0", name="content_not_empty"
        ),
        sa.CheckConstraint("length(trim(parser_version)) > 0", name="parser_version_set"),
    )
    op.create_index(
        "ix_pr_platform_policy_snapshots_content_sha256",
        "pr_platform_policy_snapshots",
        ["content_sha256"],
    )
    op.create_index(
        "ix_pr_platform_policy_snapshots_source_fetched",
        "pr_platform_policy_snapshots",
        ["source_id", "fetched_at"],
    )
    # Re-reading an unchanged page creates nothing.
    op.create_index(
        "uq_pr_platform_policy_snapshots_source_hash",
        "pr_platform_policy_snapshots",
        ["source_id", "content_sha256"],
        unique=True,
    )

    # --- Versioned packs -----------------------------------------------------
    op.create_table(
        "pr_platform_policy_packs",
        sa.Column("platform_code", sa.String(length=64), nullable=False),
        sa.Column("distribution_mode", _distribution_mode(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column(
            "status",
            sa.Enum(*PACK_STATUSES, name="pr_policy_pack_status", native_enum=False, length=20),
            nullable=False,
        ),
        sa.Column("manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_pr_platform_policy_packs"),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            [f"{USERS}.id"],
            name="fk_policy_pack_created_by",
            ondelete=RESTRICT,
        ),
        sa.CheckConstraint("version >= 1", name="version_positive"),
        sa.CheckConstraint("length(trim(platform_code)) > 0", name="platform_code_set"),
        sa.CheckConstraint("length(trim(label)) > 0", name="label_not_empty"),
        sa.CheckConstraint("length(manifest_hash) = 64", name="manifest_hash_length"),
        sa.CheckConstraint(
            "activated_at IS NOT NULL OR retired_at IS NULL", name="retired_needs_active"
        ),
        sa.CheckConstraint(
            "distribution_mode IN ('ORGANIC', 'PAID_AD')", name="mode_is_specified"
        ),
    )
    op.create_index(
        "ix_pr_platform_policy_packs_platform_code",
        "pr_platform_policy_packs",
        ["platform_code"],
    )
    op.create_index("ix_pr_platform_policy_packs_status", "pr_platform_policy_packs", ["status"])
    op.create_index(
        "uq_pr_platform_policy_packs_version",
        "pr_platform_policy_packs",
        ["platform_code", "distribution_mode", "version"],
        unique=True,
    )
    # At most one ACTIVE pack per platform and mode. Partial, so retired and
    # draft versions accumulate as history.
    op.create_index(
        "uq_pr_platform_policy_packs_active",
        "pr_platform_policy_packs",
        ["platform_code", "distribution_mode"],
        unique=True,
        postgresql_where=sa.text(ACTIVE_PACK_PREDICATE),
        sqlite_where=sa.text(ACTIVE_PACK_PREDICATE),
    )

    # --- Rules, each with provenance ----------------------------------------
    op.create_table(
        "pr_platform_policy_rules",
        sa.Column("pack_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("rule_id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("policy_scope", _policy_scope(), nullable=False),
        sa.Column("rule_text", sa.Text(), nullable=False),
        # NOT NULL: a rule with no official source cannot be stored.
        sa.Column("source_snapshot_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("section_path", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_pr_platform_policy_rules"),
        sa.ForeignKeyConstraint(
            ["pack_id"],
            ["pr_platform_policy_packs.id"],
            name="fk_policy_rule_pack",
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["source_snapshot_id"],
            ["pr_platform_policy_snapshots.id"],
            name="fk_policy_rule_snapshot",
            ondelete=RESTRICT,
        ),
        sa.CheckConstraint("length(trim(rule_id)) > 0", name="rule_id_not_empty"),
        sa.CheckConstraint("length(trim(title)) > 0", name="title_not_empty"),
        sa.CheckConstraint("length(trim(rule_text)) > 0", name="rule_text_not_empty"),
    )
    op.create_index("ix_pr_platform_policy_rules_pack", "pr_platform_policy_rules", ["pack_id"])
    op.create_index(
        "uq_pr_platform_policy_rules_pack_rule",
        "pr_platform_policy_rules",
        ["pack_id", "rule_id"],
        unique=True,
    )

    # --- Run pinning ---------------------------------------------------------
    op.create_table(
        "pr_ai_review_run_policy_packs",
        sa.Column("run_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("policy_pack_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("platform_code", sa.String(length=64), nullable=False),
        sa.Column("distribution_mode", _distribution_mode(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_pr_ai_review_run_policy_packs"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            [f"{AI_REVIEW_RUNS}.id"],
            name="fk_run_policy_pack_run",
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["policy_pack_id"],
            ["pr_platform_policy_packs.id"],
            name="fk_run_policy_pack_pack",
            ondelete=RESTRICT,
        ),
    )
    op.create_index(
        "ix_pr_ai_review_run_policy_packs_run", "pr_ai_review_run_policy_packs", ["run_id"]
    )
    op.create_index(
        "uq_pr_ai_review_run_policy_packs_context",
        "pr_ai_review_run_policy_packs",
        ["run_id", "platform_code", "distribution_mode"],
        unique=True,
    )


def downgrade() -> None:
    """Drop the policy chain and the target column.

    Read the module docstring on what this loses: every distribution mode an
    operator set, and every ingested snapshot and pack. Recorded AI reviews
    survive - ``pr_ai_reviews`` is untouched - and simply stop being able to
    name the pack they used.
    """
    op.drop_index(
        "uq_pr_ai_review_run_policy_packs_context", table_name="pr_ai_review_run_policy_packs"
    )
    op.drop_index(
        "ix_pr_ai_review_run_policy_packs_run", table_name="pr_ai_review_run_policy_packs"
    )
    op.drop_table("pr_ai_review_run_policy_packs")

    op.drop_index("uq_pr_platform_policy_rules_pack_rule", table_name="pr_platform_policy_rules")
    op.drop_index("ix_pr_platform_policy_rules_pack", table_name="pr_platform_policy_rules")
    op.drop_table("pr_platform_policy_rules")

    op.drop_index("uq_pr_platform_policy_packs_active", table_name="pr_platform_policy_packs")
    op.drop_index("uq_pr_platform_policy_packs_version", table_name="pr_platform_policy_packs")
    op.drop_index("ix_pr_platform_policy_packs_status", table_name="pr_platform_policy_packs")
    op.drop_index("ix_pr_platform_policy_packs_platform_code", table_name="pr_platform_policy_packs")
    op.drop_table("pr_platform_policy_packs")

    op.drop_index(
        "uq_pr_platform_policy_snapshots_source_hash", table_name="pr_platform_policy_snapshots"
    )
    op.drop_index(
        "ix_pr_platform_policy_snapshots_source_fetched", table_name="pr_platform_policy_snapshots"
    )
    op.drop_index(
        "ix_pr_platform_policy_snapshots_content_sha256", table_name="pr_platform_policy_snapshots"
    )
    op.drop_table("pr_platform_policy_snapshots")

    op.drop_index("uq_pr_platform_policy_sources_family", table_name="pr_platform_policy_sources")
    op.drop_index("ix_pr_platform_policy_sources_enabled", table_name="pr_platform_policy_sources")
    op.drop_index(
        "ix_pr_platform_policy_sources_platform_code", table_name="pr_platform_policy_sources"
    )
    op.drop_table("pr_platform_policy_sources")

    op.drop_column(CONTENT_TARGETS, "distribution_mode")
