"""Migration 0018, on a database built the way production builds one.

Everything Step 1F promises about ``pr_ai_review_runs`` is a *PostgreSQL*
promise: four ``CHECK`` constraints, four ``ON DELETE RESTRICT`` foreign keys,
and above all the **partial unique index** that makes "one active execution per
draft" a database rule rather than an application convention. A partial index is
exactly the thing an offline fixture built from the models cannot prove behaves
as intended - so this file never calls ``Base.metadata.create_all``. It creates
an empty database, runs the real Alembic chain through head, and writes rows the
way the application would.

It reuses the harness ``tests/integration/test_dispatch_migrations`` established
and every PR migration test follows.

Run it against a PostgreSQL you are willing to have scratch databases created in
and dropped from::

    export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/postgres
    uv run pytest tests/integration/test_pr_ai_review_run_migrations.py -m integration

Each fixture creates its own uniquely-named ``meobot_airun_*`` database and drops
that one afterwards. Nothing else on the server is read or written.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import func, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from meobot.core.config import Settings
from meobot.db.models.pr import PrBrand, PrContentItem
from meobot.db.models.pr_ai_review_run import PrAiReviewRun
from meobot.db.models.pr_content_version import PrContentVersion
from meobot.db.models.user import User
from meobot.db.session import Database
from meobot.domain.identity.models import Role
from meobot.domain.pr.models import (
    PrAiReviewRunStatus,
    PrAiReviewTrigger,
    PrAiReviewType,
    PrPriority,
    PrWorkflowStage,
)
from tests.integration.test_dispatch_migrations import (
    TEST_DATABASE_URL,
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

RUNS_TABLE = "pr_ai_review_runs"
CREATED_AT = datetime(2026, 8, 9, 9, 0, tzinfo=UTC)


async def _scratch_database(prefix: str) -> AsyncIterator[Database]:
    """A brand-new database with the full migration chain applied."""
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
            # FORCE closes any connection this test left behind. Only ever
            # applied to the scratch database this fixture just created.
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)'))
        await admin.dispose()


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def run_database() -> AsyncIterator[Database]:
    async for database in _scratch_database("meobot_airun"):
        yield database


@pytest_asyncio.fixture(loop_scope="module")
async def seeded(run_database: Database) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """One user, one content item and one version, for runs to point at."""
    async with run_database.transaction() as session:
        user = User(full_name="Le Tác Giả", role=Role.TEAM_LEAD)
        brand = PrBrand(code=f"BR-{uuid.uuid4().hex[:6]}", name="Brand 1F")
        session.add_all([user, brand])
        await session.flush()
        content = PrContentItem(
            code=f"CNT-2026-{uuid.uuid4().int % 1000000:06d}",
            title="Bài kiểm thử migration",
            brand_id=brand.id,
            priority=PrPriority.NORMAL,
            workflow_stage=PrWorkflowStage.AI_REVIEW,
            owner_user_id=user.id,
            created_by_user_id=user.id,
        )
        session.add(content)
        await session.flush()
        version = PrContentVersion(
            content_id=content.id,
            version_no=1,
            title=content.title,
            created_by_user_id=user.id,
        )
        session.add(version)
        await session.flush()
        return user.id, content.id, version.id


def _run(
    content_id: uuid.UUID,
    version_id: uuid.UUID,
    *,
    status: PrAiReviewRunStatus = PrAiReviewRunStatus.QUEUED,
    trigger: PrAiReviewTrigger = PrAiReviewTrigger.AUTO,
    **overrides: object,
) -> PrAiReviewRun:
    # ``trigger`` is a named parameter rather than an override, because a caller
    # passing it through ``**overrides`` collided with the default here.
    return PrAiReviewRun(
        content_id=content_id,
        content_version_id=version_id,
        review_type=PrAiReviewType.FULL_REVIEW,
        trigger=trigger,
        status=status,
        prompt_version="pr-full-review-v1",
        attempt_count=0,
        **overrides,  # type: ignore[arg-type]
    )


# --- The chain ---------------------------------------------------------------


async def test_the_chain_reaches_head_with_the_runs_table(run_database: Database) -> None:
    """0018 applies on top of the real 0017, on a real PostgreSQL."""
    async with run_database.engine.connect() as connection:
        tables = await connection.run_sync(lambda sync: inspect(sync).get_table_names())
        revision = await connection.execute(text("SELECT version_num FROM alembic_version"))
    assert RUNS_TABLE in tables
    # And every table the previous seventeen revisions built is still there.
    for existing in ("pr_ai_reviews", "pr_content_items", "pr_content_versions", "web_sessions"):
        assert existing in tables, existing
    assert revision.scalar() is not None


async def test_the_table_has_the_columns_it_promised(run_database: Database) -> None:
    """The durable execution record, column by column."""
    async with run_database.engine.connect() as connection:
        columns = await connection.run_sync(
            lambda sync: {
                column["name"]: column for column in inspect(sync).get_columns(RUNS_TABLE)
            }
        )
    expected_nullable = {
        "id": False,
        "content_id": False,
        "content_version_id": False,
        "review_type": False,
        "trigger": False,
        "status": False,
        "requested_by_user_id": True,
        "model_name": True,
        "model_version": True,
        "prompt_version": False,
        "attempt_count": False,
        "review_id": True,
        "outcome": True,
        "error_code": True,
        "created_at": False,
        "started_at": True,
        "finished_at": True,
        "updated_at": False,
    }
    assert set(columns) == set(expected_nullable)
    for name, nullable in expected_nullable.items():
        assert columns[name]["nullable"] is nullable, name


async def test_the_active_index_is_unique_and_partial(run_database: Database) -> None:
    """The idempotency rule, as PostgreSQL actually stored it.

    Both halves matter. Unique, or two workers review one draft twice. Partial,
    or a failed attempt occupies the slot for ever and nothing can be retried.
    """
    async with run_database.engine.connect() as connection:
        result = await connection.execute(
            text("SELECT indexdef FROM pg_indexes WHERE indexname = 'uq_pr_ai_review_runs_active'")
        )
        definition = result.scalar()
    assert definition is not None
    assert "UNIQUE INDEX" in definition
    assert "content_id" in definition and "content_version_id" in definition
    assert "review_type" in definition
    # The predicate: terminal rows are excluded.
    assert "WHERE" in definition
    assert "QUEUED" in definition and "RUNNING" in definition


async def test_two_active_runs_for_one_draft_are_refused(
    run_database: Database, seeded: tuple[uuid.UUID, uuid.UUID, uuid.UUID]
) -> None:
    """The constraint doing its job, against a real concurrent insert.

    This is the assertion the whole partial index exists for: entering
    ``AI_REVIEW`` twice, a double-tapped retry and a duplicated Celery delivery
    all end here rather than in two reviews of one draft.
    """
    _, content_id, version_id = seeded
    async with run_database.transaction() as session:
        session.add(_run(content_id, version_id))
        await session.flush()

    with pytest.raises(IntegrityError):
        async with run_database.transaction() as session:
            session.add(_run(content_id, version_id, status=PrAiReviewRunStatus.RUNNING))
            await session.flush()


async def test_a_terminal_run_frees_the_slot(
    run_database: Database, seeded: tuple[uuid.UUID, uuid.UUID, uuid.UUID]
) -> None:
    """A failed attempt may be followed by another, and both are kept.

    The partial predicate is what makes retry possible at all. A plain unique
    index over the same three columns would have made the first failure
    permanent.
    """
    _, content_id, version_id = seeded
    async with run_database.transaction() as session:
        first = _run(content_id, version_id, status=PrAiReviewRunStatus.FAILED)
        first.started_at = CREATED_AT
        first.finished_at = CREATED_AT
        first.error_code = "llm_error"
        session.add(first)
        await session.flush()

    async with run_database.transaction() as session:
        session.add(_run(content_id, version_id, trigger=PrAiReviewTrigger.MANUAL_RETRY))
        await session.flush()

    async with run_database.transaction() as session:
        rows = await session.execute(
            select(func.count())
            .select_from(PrAiReviewRun)
            .where(PrAiReviewRun.content_version_id == version_id)
        )
        assert rows.scalar() == 2


async def test_every_foreign_key_restricts(run_database: Database) -> None:
    """Four keys, none of them cascading, exactly as everywhere else here.

    Deleting a reviewed content item, the draft that was read, or the person
    who asked fails rather than quietly discarding the record of what ran.
    """
    async with run_database.engine.connect() as connection:
        keys = await connection.run_sync(lambda sync: inspect(sync).get_foreign_keys(RUNS_TABLE))
    referred = {key["referred_table"] for key in keys}
    assert referred == {"pr_content_items", "pr_content_versions", "pr_ai_reviews", "users"}
    for key in keys:
        assert key["options"].get("ondelete") == "RESTRICT", key["name"]


async def test_a_negative_attempt_count_is_refused(
    run_database: Database, seeded: tuple[uuid.UUID, uuid.UUID, uuid.UUID]
) -> None:
    """One of the four checks, proved rather than assumed."""
    _, content_id, version_id = seeded
    with pytest.raises(IntegrityError):
        async with run_database.transaction() as session:
            run = _run(content_id, version_id, status=PrAiReviewRunStatus.SUCCEEDED)
            run.attempt_count = -1
            run.started_at = CREATED_AT
            run.finished_at = CREATED_AT
            session.add(run)
            await session.flush()


async def test_a_finished_run_must_have_started(
    run_database: Database, seeded: tuple[uuid.UUID, uuid.UUID, uuid.UUID]
) -> None:
    """A run cannot claim an end it never began."""
    _, content_id, version_id = seeded
    with pytest.raises(IntegrityError):
        async with run_database.transaction() as session:
            run = _run(content_id, version_id, status=PrAiReviewRunStatus.FAILED)
            run.finished_at = CREATED_AT
            session.add(run)
            await session.flush()


# --- Downgrade ---------------------------------------------------------------


async def test_downgrading_0018_keeps_every_recorded_review() -> None:
    """The point of the two-table design, proved.

    Execution history is lost on downgrade and reviews are not - ``pr_ai_reviews``
    is untouched by both directions of this revision. Automatic review simply
    stops happening, which is the pre-Step-1F behaviour.
    """
    async for database in _scratch_database("meobot_airun_down"):
        async with database.engine.connect() as connection:
            before = await connection.run_sync(lambda sync: inspect(sync).get_table_names())
        assert RUNS_TABLE in before

        dsn = str(database.engine.url.render_as_string(hide_password=False))
        await downgrade_to(dsn, "0017")

        async with database.engine.connect() as connection:
            after = await connection.run_sync(lambda sync: inspect(sync).get_table_names())
        assert RUNS_TABLE not in after
        # Everything Step 1A1 built is still standing.
        assert "pr_ai_reviews" in after
        assert "pr_content_versions" in after

        await upgrade_to(dsn, "head")


# --- Parity ------------------------------------------------------------------


def test_the_migration_vocabulary_matches_the_enums() -> None:
    """The migration's frozen literals against the live enums.

    The migration holds tuples on purpose - it must keep meaning what it meant
    on the day it ran - so a drift check is the only thing that keeps the two
    honest. Same rider every PR revision carries.
    """
    import re
    from pathlib import Path

    source = Path("alembic/versions/0018_pr_ai_review_runs.py").read_text(encoding="utf-8")

    def listed(name: str) -> tuple[str, ...]:
        match = re.search(rf"{name} = \(([^)]*)\)", source)
        assert match is not None, name
        return tuple(re.findall(r'"([A-Z_]+)"', match.group(1)))

    assert listed("RUN_STATUSES") == tuple(status.value for status in PrAiReviewRunStatus)
    assert listed("RUN_TRIGGERS") == tuple(trigger.value for trigger in PrAiReviewTrigger)
    assert listed("REVIEW_TYPES") == tuple(kind.value for kind in PrAiReviewType)
