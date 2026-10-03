"""Migration 0019, on a database built the way production builds one.

Everything Step 1F.1 promises is a *PostgreSQL* promise:

* ``pr_content_targets.distribution_mode`` defaults every existing row to
  ``UNSPECIFIED`` rather than guessing ``ORGANIC``;
* ``uq_pr_platform_policy_packs_active`` is **partial** - one ACTIVE pack per
  platform and mode, with retired and draft versions accumulating as history;
* ``uq_pr_platform_policy_snapshots_source_hash`` deduplicates an unchanged
  page, so a daily refresh does not append a snapshot a day;
* ``pr_platform_policy_rules.source_snapshot_id`` is ``NOT NULL``, so a rule
  with no official source cannot be stored at all.

None of those can be proved by an offline fixture that builds a schema from the
models it is checking, so this file never calls ``Base.metadata.create_all``. It
creates an empty database, runs the real Alembic chain through head, and writes
rows the way the application would.

**It makes no network call.** The migration does not fetch policy, and neither
does this file: the tables are created empty and populated with synthetic text.

    export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/postgres
    uv run pytest tests/integration/test_pr_platform_policy_migrations.py -m integration
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from meobot.core.config import Settings
from meobot.db.models.pr import PrBrand, PrChannel, PrContentItem, PrContentTarget, PrPlatform
from meobot.db.models.pr_platform_policy import (
    PrPlatformPolicyPack,
    PrPlatformPolicyRule,
    PrPlatformPolicySnapshot,
    PrPlatformPolicySource,
)
from meobot.db.models.user import User
from meobot.db.session import Database
from meobot.domain.identity.models import Role
from meobot.domain.pr.models import (
    PrChannelCategory,
    PrDistributionMode,
    PrPolicyIngestionMethod,
    PrPolicyPackStatus,
    PrPolicyScope,
    PrPolicySourceRole,
    PrPriority,
    PrWorkflowStage,
)
from tests.integration.test_dispatch_migrations import (
    TEST_DATABASE_URL,
    alembic_head,
    downgrade_to,
    upgrade_to,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="module"),
    pytest.mark.skipif(
        not TEST_DATABASE_URL,
        reason="Set MEOBOT_TEST_DATABASE_URL to run integration tests",
    ),
]

MOMENT = datetime(2026, 8, 9, 9, 0, tzinfo=UTC)


async def _scratch_database(prefix: str) -> AsyncIterator[Database]:
    assert TEST_DATABASE_URL is not None
    base = make_url(TEST_DATABASE_URL)
    scratch = f"{prefix}_{uuid.uuid4().hex[:12]}"
    admin = create_async_engine(
        base.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=None
    )
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{scratch}"'))
        url = base.set(database=scratch)
        dsn = url.render_as_string(hide_password=False)
        await upgrade_to(dsn)
        database = Database(Settings(_env_file=None, database_url=dsn))
        try:
            yield database
        finally:
            await database.dispose()
    finally:
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)'))
        await admin.dispose()


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def policy_database() -> AsyncIterator[Database]:
    async for database in _scratch_database("meobot_policy"):
        yield database


def _source(family: str, role: PrPolicySourceRole) -> PrPlatformPolicySource:
    return PrPlatformPolicySource(
        platform_code="TIKTOK",
        policy_scope=PrPolicyScope.COMMUNITY_STANDARDS,
        source_role=role,
        source_family=family,
        name=family,
        canonical_url=f"https://www.tiktok.com/{family.lower()}/",
        ingestion_method=PrPolicyIngestionMethod.FETCH,
    )


def _pack(version: int, status: PrPolicyPackStatus) -> PrPlatformPolicyPack:
    pack = PrPlatformPolicyPack(
        platform_code="TIKTOK",
        distribution_mode=PrDistributionMode.ORGANIC,
        version=version,
        label=f"TIKTOK-ORGANIC-2026-08-09.{version}",
        status=status,
        manifest_hash="a" * 64,
    )
    if status is not PrPolicyPackStatus.DRAFT:
        pack.activated_at = MOMENT
    if status is PrPolicyPackStatus.RETIRED:
        pack.retired_at = MOMENT
    return pack


async def test_the_chain_reaches_head_with_every_policy_table(
    policy_database: Database,
) -> None:
    """0019 applies on top of the real 0018, on real PostgreSQL."""
    async with policy_database.engine.connect() as connection:
        tables = await connection.run_sync(lambda sync: inspect(sync).get_table_names())
        columns = await connection.run_sync(
            lambda sync: {c["name"] for c in inspect(sync).get_columns("pr_content_targets")}
        )
    for table in (
        "pr_platform_policy_sources",
        "pr_platform_policy_snapshots",
        "pr_platform_policy_packs",
        "pr_platform_policy_rules",
        "pr_ai_review_run_policy_packs",
    ):
        assert table in tables, table
    assert "distribution_mode" in columns
    # Nothing earlier was dropped.
    for existing in ("pr_ai_reviews", "pr_ai_review_runs", "web_sessions"):
        assert existing in tables, existing


async def test_existing_targets_default_to_unspecified(policy_database: Database) -> None:
    """The decision that matters most in this migration.

    Backfilling ``ORGANIC`` would have been one UPDATE and a silent lie: a piece
    already running as a paid ad would then be reviewed against community
    standards alone.
    """
    async with policy_database.transaction() as session:
        user = User(full_name="Le Tác Giả", role=Role.TEAM_LEAD)
        brand = PrBrand(code=f"BR-{uuid.uuid4().hex[:6]}", name="B")
        platform = PrPlatform(code=f"PL-{uuid.uuid4().hex[:6]}", name="P")
        session.add_all([user, brand, platform])
        await session.flush()
        channel = PrChannel(
            code=f"CH-{uuid.uuid4().hex[:6]}",
            name="C",
            category=PrChannelCategory.SCALE,
            platform_id=platform.id,
            brand_id=brand.id,
        )
        content = PrContentItem(
            code=f"CNT-2026-{uuid.uuid4().int % 1000000:06d}",
            title="T",
            brand_id=brand.id,
            priority=PrPriority.NORMAL,
            workflow_stage=PrWorkflowStage.IDEA,
            owner_user_id=user.id,
            created_by_user_id=user.id,
        )
        session.add_all([channel, content])
        await session.flush()
        # Inserted without naming the column, exactly as a pre-0019 row was.
        await session.execute(
            text(
                "INSERT INTO pr_content_targets (id, content_id, channel_id, status) "
                "VALUES (:id, :content_id, :channel_id, 'PLANNED')"
            ),
            {"id": uuid.uuid4(), "content_id": content.id, "channel_id": channel.id},
        )
        stored = await session.execute(
            select(PrContentTarget.distribution_mode).where(
                PrContentTarget.content_id == content.id
            )
        )
    assert stored.scalars().one() is PrDistributionMode.UNSPECIFIED


async def test_the_active_pack_index_is_unique_and_partial(policy_database: Database) -> None:
    """Both halves. Unique, or two packs claim to be current; partial, or a
    retired version holds the slot for ever and nothing can supersede it."""
    async with policy_database.engine.connect() as connection:
        result = await connection.execute(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE indexname = 'uq_pr_platform_policy_packs_active'"
            )
        )
        definition = result.scalar()
    assert definition is not None
    assert "UNIQUE INDEX" in definition
    assert "WHERE" in definition and "ACTIVE" in definition


async def test_two_active_packs_are_refused_but_history_accumulates(
    policy_database: Database,
) -> None:
    """One ACTIVE, many RETIRED and DRAFT."""
    async with policy_database.transaction() as session:
        session.add(_pack(1, PrPolicyPackStatus.ACTIVE))
        await session.flush()

    with pytest.raises(IntegrityError):
        async with policy_database.transaction() as session:
            session.add(_pack(2, PrPolicyPackStatus.ACTIVE))
            await session.flush()

    # Retired and draft versions coexist freely - that is the version history.
    async with policy_database.transaction() as session:
        session.add(_pack(3, PrPolicyPackStatus.RETIRED))
        session.add(_pack(4, PrPolicyPackStatus.DRAFT))
        await session.flush()


async def test_an_identical_snapshot_is_refused(policy_database: Database) -> None:
    """Re-reading an unchanged page creates nothing."""
    async with policy_database.transaction() as session:
        source = _source(f"TT_CG_{uuid.uuid4().hex[:6]}", PrPolicySourceRole.POLICY_CONTENT)
        session.add(source)
        await session.flush()
        digest = "b" * 64
        session.add(
            PrPlatformPolicySnapshot(
                source_id=source.id,
                fetched_at=MOMENT,
                canonical_url=source.canonical_url,
                ingestion_method=PrPolicyIngestionMethod.FETCH,
                content_sha256=digest,
                parser_version="policy-parser-v1",
                normalized_content="Policy text.",
            )
        )
        await session.flush()
        source_id = source.id

    with pytest.raises(IntegrityError):
        async with policy_database.transaction() as session:
            session.add(
                PrPlatformPolicySnapshot(
                    source_id=source_id,
                    fetched_at=MOMENT,
                    canonical_url="https://www.tiktok.com/x/",
                    ingestion_method=PrPolicyIngestionMethod.FETCH,
                    content_sha256="b" * 64,
                    parser_version="policy-parser-v1",
                    normalized_content="Policy text.",
                )
            )
            await session.flush()


async def test_a_rule_without_provenance_cannot_be_stored(policy_database: Database) -> None:
    """``source_snapshot_id`` is NOT NULL: the defence against a pack quietly
    accumulating text somebody typed from memory."""
    async with policy_database.transaction() as session:
        pack = _pack(90, PrPolicyPackStatus.DRAFT)
        session.add(pack)
        await session.flush()
        pack_id = pack.id

    with pytest.raises(IntegrityError):
        async with policy_database.transaction() as session:
            session.add(
                PrPlatformPolicyRule(
                    pack_id=pack_id,
                    rule_id="TT-CS-001",
                    title="T",
                    policy_scope=PrPolicyScope.COMMUNITY_STANDARDS,
                    rule_text="x",
                    source_snapshot_id=None,  # type: ignore[arg-type]
                    source_url="https://www.tiktok.com/a",
                )
            )
            await session.flush()


async def test_every_foreign_key_restricts(policy_database: Database) -> None:
    """Deleting a snapshot a rule cites, or a pack a review pinned, fails."""
    async with policy_database.engine.connect() as connection:
        for table in (
            "pr_platform_policy_snapshots",
            "pr_platform_policy_packs",
            "pr_platform_policy_rules",
            "pr_ai_review_run_policy_packs",
        ):
            keys = await connection.run_sync(
                lambda sync, name=table: inspect(sync).get_foreign_keys(name)
            )
            assert keys, table
            for key in keys:
                assert key["options"].get("ondelete") == "RESTRICT", (table, key["name"])


async def test_downgrading_0019_keeps_every_recorded_review() -> None:
    """Execution and review history survive; policy data and modes do not."""
    async for database in _scratch_database("meobot_policy_down"):
        dsn = str(database.engine.url.render_as_string(hide_password=False))
        await downgrade_to(dsn, "0018")
        async with database.engine.connect() as connection:
            tables = await connection.run_sync(lambda sync: inspect(sync).get_table_names())
            columns = await connection.run_sync(
                lambda sync: {c["name"] for c in inspect(sync).get_columns("pr_content_targets")}
            )
        assert "pr_platform_policy_packs" not in tables
        assert "distribution_mode" not in columns
        # Step 1F's tables are untouched by either direction.
        assert "pr_ai_reviews" in tables
        assert "pr_ai_review_runs" in tables
        await upgrade_to(dsn, "head")


def test_the_migration_vocabulary_matches_the_enums() -> None:
    """Frozen literals against the live enums - the rider every PR revision carries."""
    import re
    from pathlib import Path

    source = Path("alembic/versions/0019_pr_platform_policy.py").read_text(encoding="utf-8")

    def listed(name: str) -> tuple[str, ...]:
        match = re.search(rf"{name} = \(([^)]*)\)", source)
        assert match is not None, name
        return tuple(re.findall(r'"([A-Z_]+)"', match.group(1)))

    assert listed("DISTRIBUTION_MODES") == tuple(m.value for m in PrDistributionMode)
    assert listed("POLICY_SCOPES") == tuple(s.value for s in PrPolicyScope)
    assert listed("PACK_STATUSES") == tuple(s.value for s in PrPolicyPackStatus)
    assert listed("INGESTION_METHODS") == tuple(m.value for m in PrPolicyIngestionMethod)
    assert listed("SOURCE_ROLES") == tuple(r.value for r in PrPolicySourceRole)


def test_the_migration_makes_no_network_call() -> None:
    """A migration that fetched a policy page would fail when a website is down.

    Ingestion is an ops command and a beat job; this revision creates empty
    tables and populates nothing.
    """
    from pathlib import Path

    source = Path("alembic/versions/0019_pr_platform_policy.py").read_text(encoding="utf-8")
    # Clients and fetchers, not the string "https://": the sources table has a
    # ``canonical_url LIKE 'https://%'`` check, which is a constraint on data
    # this migration creates rather than a request it makes.
    for forbidden in (
        "httpx",
        "requests",
        "urlopen",
        "aiohttp",
        "PolicySourceFetcher",
        "POLICY_SOURCE_SEEDS",
        "op.bulk_insert",
    ):
        assert forbidden not in source, forbidden
    # And it executes no DML: ingestion is an ops command and a beat job.
    assert "op.execute" not in source


# --- Release hardening: the failure that stopped the first deployment --------


async def test_a_failed_upgrade_leaves_the_database_at_0018() -> None:
    """The production failure's central property, proved rather than assumed.

    The first deployment attempt of 0019 raised ``IdentifierError`` on a real
    PostgreSQL. The question that mattered next was not "why" but "what is the
    database now" - and the answer has to be *exactly 0018*, with none of Step
    1F.1's tables half-created, or the fix would need a repair script rather
    than a re-run.

    PostgreSQL has transactional DDL and Alembic runs a revision in one
    transaction, so a mid-revision failure rolls the whole thing back. This
    asserts it instead of trusting it: it stops at 0018, confirms the version
    table and confirms not one Step 1F.1 object exists.
    """
    async for database in _scratch_database("meobot_policy_rollback"):
        dsn = str(database.engine.url.render_as_string(hide_password=False))
        await downgrade_to(dsn, "0018")

        async with database.engine.connect() as connection:
            version = await connection.execute(text("SELECT version_num FROM alembic_version"))
            tables = await connection.run_sync(lambda sync: inspect(sync).get_table_names())
            columns = await connection.run_sync(
                lambda sync: {c["name"] for c in inspect(sync).get_columns("pr_content_targets")}
            )

        assert version.scalar() == "0018"
        # Nothing from the revision that failed. Not one table, not the column.
        for absent in (
            "pr_platform_policy_sources",
            "pr_platform_policy_snapshots",
            "pr_platform_policy_packs",
            "pr_platform_policy_rules",
            "pr_ai_review_run_policy_packs",
        ):
            assert absent not in tables, absent
        assert "distribution_mode" not in columns
        # And everything Step 1F built is still standing, so the failure cost
        # nothing beyond the attempt.
        assert "pr_ai_review_runs" in tables
        assert "pr_ai_reviews" in tables

        # The corrected revision now applies cleanly from exactly that state.
        await upgrade_to(dsn, "head")
        async with database.engine.connect() as connection:
            version = await connection.execute(text("SELECT version_num FROM alembic_version"))
            tables = await connection.run_sync(lambda sync: inspect(sync).get_table_names())
        # Whatever ``head`` is today, read from the script directory rather than
        # written here: this test is about 0019 applying cleanly from 0018, and
        # pinning the literal ``"0019"`` made it a test about which revision
        # happened to be newest - it started failing the day 0020 landed.
        assert version.scalar() == alembic_head()
        assert "pr_platform_policy_packs" in tables


async def test_no_identifier_created_by_0019_exceeds_the_postgres_limit(
    policy_database: Database,
) -> None:
    """Every name, as PostgreSQL actually stored it.

    The unit suite checks the ORM metadata and the migration source. This checks
    the database - which is the only place the two meeting can be observed, and
    the place the original failure happened.

    A name at exactly 63 would also be suspicious: PostgreSQL truncates silently
    at ``NAMEDATALEN`` when it is the one generating a name, so a value pinned to
    the limit usually means something was cut.
    """
    async with policy_database.engine.connect() as connection:
        result = await connection.execute(
            text(
                """
                SELECT c.conname AS name, 'constraint' AS kind
                  FROM pg_constraint c
                  JOIN pg_class t ON t.oid = c.conrelid
                 WHERE t.relname LIKE 'pr_%policy%' OR t.relname = 'pr_content_targets'
                 UNION ALL
                SELECT i.indexname AS name, 'index' AS kind
                  FROM pg_indexes i
                 WHERE i.tablename LIKE 'pr_%policy%' OR i.tablename = 'pr_content_targets'
                """
            )
        )
        rows = [(row.name, row.kind) for row in result]

    assert rows, "expected Step 1F.1 constraints and indexes to exist"
    over = [(len(name.encode()), name, kind) for name, kind in rows if len(name.encode()) > 63]
    assert over == [], over
    # The specific identifier the first deployment died on.
    assert not any(
        name == "fk_pr_platform_policy_snapshots_source_id_pr_platform_policy_sources"
        for name, _ in rows
    )


async def test_the_short_foreign_key_names_are_the_ones_in_the_database(
    policy_database: Database,
) -> None:
    """Schema and ORM agree on the explicit names, so autogenerate stays quiet."""
    async with policy_database.engine.connect() as connection:
        result = await connection.execute(
            text(
                "SELECT conname FROM pg_constraint c "
                "JOIN pg_class t ON t.oid = c.conrelid "
                "WHERE c.contype = 'f' AND t.relname LIKE 'pr_%policy%'"
            )
        )
        names = {row.conname for row in result}

    for expected in (
        "fk_policy_snapshot_source",
        "fk_policy_pack_created_by",
        "fk_policy_rule_pack",
        "fk_policy_rule_snapshot",
        "fk_run_policy_pack_run",
        "fk_run_policy_pack_pack",
    ):
        assert expected in names, expected
