"""The PR core foundation, on a database built the way production builds one.

Every constraint Step 1A promises is a *PostgreSQL* constraint: a partial
unique index, a ``CHECK`` on a ``NUMERIC``, an ``ON DELETE RESTRICT``. None of
them can be proved by the offline SQLite fixture, and the 0.6.0a3 incident is
the standing reminder of what happens when a schema is only ever checked
against the models that generated it. So this file does not use
``Base.metadata.create_all`` at all: it creates an empty database, runs the
real Alembic chain through head, and writes rows the way an application would.

It reuses the migration harness ``tests/integration/test_dispatch_migrations``
already established - the same scratch-database-per-run approach, the same
``asyncio.to_thread`` trick for Alembic's own event loop.

Run it against a PostgreSQL you are willing to have scratch databases created
in and dropped from::

    export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/postgres
    uv run pytest tests/integration/test_pr_core_migrations.py -m integration

Each fixture creates its own uniquely-named ``meobot_pr_*`` database and drops
that one afterwards. Nothing else on the server is read or written.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from meobot.core.config import Settings
from meobot.db.base import Base
from meobot.db.models.pr import (
    PrApprovalEvent,
    PrBrand,
    PrChannel,
    PrChannelAssignment,
    PrContentFormat,
    PrContentItem,
    PrContentPillar,
    PrContentTarget,
    PrPlatform,
    PrTask,
    PrTaskAssignment,
)
from meobot.db.models.user import User
from meobot.db.session import Database
from meobot.domain.identity.models import Role
from meobot.domain.pr.models import (
    PrApprovalDecision,
    PrApprovalStage,
    PrChannelAssignmentRole,
    PrChannelCategory,
    PrTaskAssignmentRole,
)
from tests.integration.test_dispatch_migrations import (
    TEST_DATABASE_URL,
    downgrade_to,
    upgrade_to,
)

ROOT = Path(__file__).resolve().parents[2]

# The migrated database is built once for the whole module - twelve revisions
# is too much to pay per test - so every coroutine here has to run on the same
# event loop the fixture was created on. ``loop_scope="module"`` is what pins
# that; without it pytest-asyncio gives each test a fresh loop and the shared
# connection pool belongs to a loop that has already closed.
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="module"),
    pytest.mark.skipif(
        not TEST_DATABASE_URL,
        reason="Set MEOBOT_TEST_DATABASE_URL to run integration tests",
    ),
]

#: The eleven tables revision 0012 creates, in dependency order.
PR_TABLES: tuple[str, ...] = (
    "pr_brands",
    "pr_platforms",
    "pr_content_formats",
    "pr_content_pillars",
    "pr_channels",
    "pr_channel_assignments",
    "pr_content_items",
    "pr_content_targets",
    "pr_tasks",
    "pr_task_assignments",
    "pr_approval_events",
)


async def _scratch_database(prefix: str) -> AsyncIterator[Database]:
    """A brand-new database with the full migration chain applied."""
    assert TEST_DATABASE_URL is not None
    base = make_url(TEST_DATABASE_URL)
    scratch = f"{prefix}_{uuid.uuid4().hex[:12]}"

    # CREATE/DROP DATABASE cannot run inside a transaction, hence AUTOCOMMIT.
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


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def pr_database() -> AsyncIterator[Database]:
    """One migrated database shared by the constraint tests.

    Module-scoped because migrating a blank database runs twelve revisions, and
    the constraint tests are independent: each one uses its own codes and its
    own rows, and the failures they provoke are rolled back by
    :meth:`Database.transaction`.
    """
    async for database in _scratch_database("meobot_pr"):
        yield database


@pytest_asyncio.fixture(loop_scope="module")
async def fresh_pr_database() -> AsyncIterator[Database]:
    """A migrated database of its own, for tests that move the schema."""
    async for database in _scratch_database("meobot_prmig"):
        yield database


# --- Fixtures that build the rows the constraint tests hang off ------------


async def _make_user(database: Database, name: str) -> uuid.UUID:
    async with database.transaction() as session:
        user = User(full_name=name, role=Role.EMPLOYEE)
        session.add(user)
        await session.flush()
        return user.id


async def _make_brand(database: Database, code: str) -> uuid.UUID:
    async with database.transaction() as session:
        brand = PrBrand(code=code, name=f"Brand {code}")
        session.add(brand)
        await session.flush()
        return brand.id


async def _make_platform(database: Database, code: str) -> uuid.UUID:
    async with database.transaction() as session:
        platform = PrPlatform(code=code, name=f"Platform {code}")
        session.add(platform)
        await session.flush()
        return platform.id


async def _make_channel(
    database: Database, code: str, *, tier: int | None = 1
) -> tuple[uuid.UUID, uuid.UUID]:
    platform_id = await _make_platform(database, f"PLAT-{code}")
    brand_id = await _make_brand(database, f"BRND-{code}")
    async with database.transaction() as session:
        channel = PrChannel(
            code=code,
            name=f"Channel {code}",
            platform_id=platform_id,
            brand_id=brand_id,
            tier=tier,
            category=PrChannelCategory.SCALE,
        )
        session.add(channel)
        await session.flush()
        return channel.id, brand_id


async def _make_content(database: Database, code: str, brand_id: uuid.UUID) -> uuid.UUID:
    user_id = await _make_user(database, f"Owner {code}")
    async with database.transaction() as session:
        item = PrContentItem(
            code=code,
            title=f"Content {code}",
            brand_id=brand_id,
            owner_user_id=user_id,
            created_by_user_id=user_id,
        )
        session.add(item)
        await session.flush()
        return item.id


async def _make_task(database: Database, code: str) -> uuid.UUID:
    user_id = await _make_user(database, f"Creator {code}")
    async with database.transaction() as session:
        task = PrTask(
            code=code, task_type="SCRIPT", title=f"Task {code}", created_by_user_id=user_id
        )
        session.add(task)
        await session.flush()
        return task.id


# --- 1 & 2: the migration goes up and comes back down ----------------------


async def test_the_migration_chain_reaches_head_with_every_pr_table(
    pr_database: Database,
) -> None:
    """Requirement 1."""
    heads = {
        path.stem.split("_")[0]
        for path in (ROOT / "alembic" / "versions").glob("*.py")
        if path.stem[0].isdigit()
    }
    async with pr_database.session() as session:
        stamped = (
            await session.execute(text("SELECT version_num FROM alembic_version"))
        ).scalar_one()
        present = {
            row[0]
            for row in (
                await session.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'public' AND table_name LIKE 'pr\\_%'"
                    )
                )
            ).all()
        }
    assert stamped == max(heads)
    # A subset, not an equality: later revisions add more ``pr_`` tables -
    # Step 1B's nine arrive in 0013 - and asserting equality here would fail
    # every time the module grows, which says nothing about Step 1A.
    # ``tests/unit/test_pr_core_schema_parity.py`` is what pins 0012's table
    # set to exactly these eleven.
    assert set(PR_TABLES) <= present


async def test_downgrading_0012_removes_every_pr_table_and_nothing_else(
    fresh_pr_database: Database,
) -> None:
    """Requirement 2.

    Also checks what the downgrade must *not* do: ``users`` and the dispatch
    tables are untouched, because a PR rollback that took the identity table
    with it would be catastrophic and silent.
    """
    dsn = str(fresh_pr_database._settings.database_url)

    async def table_names() -> set[str]:
        async with fresh_pr_database.session() as session:
            rows = await session.execute(
                text(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
                )
            )
            return {row[0] for row in rows.all()}

    # Step back to 0012 first, so that what this test measures is what *0012's*
    # downgrade removes rather than everything later revisions piled on top.
    # Without this the scratch database is at head, and 0013's nine reporting
    # tables would be counted against 0012.
    await downgrade_to(dsn, "0012")

    before = await table_names()
    assert set(PR_TABLES) <= before

    await downgrade_to(dsn, "0011")
    after = await table_names()
    assert after & set(PR_TABLES) == set()
    assert before - after == set(PR_TABLES), "the downgrade removed something else"
    assert "users" in after
    assert "message_dispatches" in after

    # And it goes back up cleanly, so 0012 is not a one-way door.
    await upgrade_to(dsn, "head")
    assert set(PR_TABLES) <= await table_names()


async def test_the_models_and_the_migration_describe_the_same_pr_schema(
    pr_database: Database,
) -> None:
    """Requirement 3, using Alembic's own comparator rather than a hand list.

    Restricted to the eleven PR tables: this repository has pre-existing drift
    between older models and older migrations, and widening this assertion
    would make it fail for reasons Step 1A did not cause and must not fix.
    """
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    def _compare(connection: Any) -> list[Any]:
        context = MigrationContext.configure(
            connection, opts={"compare_type": True, "compare_server_default": True}
        )
        return list(compare_metadata(context, Base.metadata))

    async with pr_database.engine.connect() as connection:
        differences = await connection.run_sync(_compare)

    pr_differences = [difference for difference in differences if "pr_" in str(difference)]
    assert pr_differences == [], pr_differences


# --- 4: codes are unique ---------------------------------------------------


@pytest.mark.parametrize("table", ["pr_brands", "pr_platforms", "pr_content_formats"])
async def test_a_duplicate_reference_code_is_refused(pr_database: Database, table: str) -> None:
    """Requirement 4, for the reference tables."""
    models = {
        "pr_brands": PrBrand,
        "pr_platforms": PrPlatform,
        "pr_content_formats": PrContentFormat,
    }
    model = models[table]
    code = f"DUP-{table}"
    async with pr_database.transaction() as session:
        session.add(model(code=code, name="First"))
    with pytest.raises(IntegrityError):
        async with pr_database.transaction() as session:
            session.add(model(code=code, name="Second"))
            await session.flush()


async def test_a_duplicate_pillar_code_is_refused(pr_database: Database) -> None:
    async with pr_database.transaction() as session:
        session.add(PrContentPillar(code="PILLAR-DUP", name="First"))
    with pytest.raises(IntegrityError):
        async with pr_database.transaction() as session:
            session.add(PrContentPillar(code="PILLAR-DUP", name="Second"))
            await session.flush()


async def test_a_duplicate_channel_code_is_refused(pr_database: Database) -> None:
    """``CH-0001`` names exactly one channel."""
    _, _ = await _make_channel(pr_database, "CH-0001")
    platform_id = await _make_platform(pr_database, "PLAT-CLASH")
    with pytest.raises(IntegrityError):
        async with pr_database.transaction() as session:
            session.add(
                PrChannel(
                    code="CH-0001",
                    name="Impostor",
                    platform_id=platform_id,
                    category=PrChannelCategory.TEST,
                )
            )
            await session.flush()


async def test_a_duplicate_content_code_is_refused(pr_database: Database) -> None:
    """``CNT-2026-000001`` names exactly one content item."""
    _, brand_id = await _make_channel(pr_database, "CH-CNTDUP")
    await _make_content(pr_database, "CNT-2026-000001", brand_id)
    user_id = await _make_user(pr_database, "Second author")
    with pytest.raises(IntegrityError):
        async with pr_database.transaction() as session:
            session.add(
                PrContentItem(
                    code="CNT-2026-000001",
                    title="Impostor",
                    brand_id=brand_id,
                    owner_user_id=user_id,
                    created_by_user_id=user_id,
                )
            )
            await session.flush()


async def test_a_duplicate_task_code_is_refused(pr_database: Database) -> None:
    """``TSK-2026-000001`` names exactly one task."""
    await _make_task(pr_database, "TSK-2026-000001")
    user_id = await _make_user(pr_database, "Second creator")
    with pytest.raises(IntegrityError):
        async with pr_database.transaction() as session:
            session.add(
                PrTask(
                    code="TSK-2026-000001",
                    task_type="EDIT",
                    title="Impostor",
                    created_by_user_id=user_id,
                )
            )
            await session.flush()


async def test_a_blank_code_is_refused(pr_database: Database) -> None:
    """Whitespace is exactly as useless as an empty string."""
    with pytest.raises(IntegrityError):
        async with pr_database.transaction() as session:
            session.add(PrBrand(code="   ", name="Blank"))
            await session.flush()


# --- 5: channel tier ------------------------------------------------------


@pytest.mark.parametrize("tier", [1, 2, 3, None])
async def test_a_channel_accepts_the_three_tiers_and_no_tier(
    pr_database: Database, tier: int | None
) -> None:
    """Requirement 5, the accepting half."""
    channel_id, _ = await _make_channel(pr_database, f"CH-TIER-{tier}", tier=tier)
    async with pr_database.session() as session:
        stored = (
            await session.execute(
                text("SELECT tier FROM pr_channels WHERE id = :id"), {"id": channel_id}
            )
        ).scalar_one()
    assert stored == tier


@pytest.mark.parametrize("tier", [0, 4, -1, 9])
async def test_a_channel_refuses_any_other_tier(pr_database: Database, tier: int) -> None:
    """Requirement 5, the refusing half."""
    platform_id = await _make_platform(pr_database, f"PLAT-BADTIER-{tier}")
    with pytest.raises(IntegrityError):
        async with pr_database.transaction() as session:
            session.add(
                PrChannel(
                    code=f"CH-BADTIER-{tier}",
                    name="Bad tier",
                    platform_id=platform_id,
                    tier=tier,
                    category=PrChannelCategory.TEST,
                )
            )
            await session.flush()


# --- 6, 7, 8: channel assignments ------------------------------------------


@pytest.mark.parametrize("percent", ["-0.01", "-1", "100.01", "150"])
async def test_an_allocation_outside_zero_to_one_hundred_is_refused(
    pr_database: Database, percent: str
) -> None:
    """Requirement 6."""
    channel_id, _ = await _make_channel(pr_database, f"CH-ALLOC-{percent}")
    user_id = await _make_user(pr_database, f"Allocatee {percent}")
    with pytest.raises(IntegrityError):
        async with pr_database.transaction() as session:
            session.add(
                PrChannelAssignment(
                    channel_id=channel_id,
                    user_id=user_id,
                    assignment_role=PrChannelAssignmentRole.CHANNEL_OWNER,
                    allocation_percent=Decimal(percent),
                    effective_from=date(2026, 1, 1),
                )
            )
            await session.flush()


@pytest.mark.parametrize("percent", ["0", "0.5", "50", "100"])
async def test_an_allocation_within_range_is_accepted(pr_database: Database, percent: str) -> None:
    channel_id, _ = await _make_channel(pr_database, f"CH-OKALLOC-{percent}")
    user_id = await _make_user(pr_database, f"Allocatee {percent}")
    async with pr_database.transaction() as session:
        session.add(
            PrChannelAssignment(
                channel_id=channel_id,
                user_id=user_id,
                assignment_role=PrChannelAssignmentRole.CONTENT_OWNER,
                allocation_percent=Decimal(percent),
                effective_from=date(2026, 1, 1),
            )
        )
        await session.flush()


async def test_percentages_are_not_required_to_sum_to_one_hundred(
    pr_database: Database,
) -> None:
    """Deliberately unconstrained.

    The database cannot know whether an over-allocated week is an error or a
    fact, and refusing the insert would make it impossible to record what is
    actually happening. Whether the totals are sensible is a Step 1B question.
    """
    channel_a, _ = await _make_channel(pr_database, "CH-SUM-A")
    channel_b, _ = await _make_channel(pr_database, "CH-SUM-B")
    user_id = await _make_user(pr_database, "Overcommitted")
    async with pr_database.transaction() as session:
        for channel_id in (channel_a, channel_b):
            session.add(
                PrChannelAssignment(
                    channel_id=channel_id,
                    user_id=user_id,
                    assignment_role=PrChannelAssignmentRole.PRODUCTION_OWNER,
                    allocation_percent=Decimal("80"),
                    effective_from=date(2026, 1, 1),
                )
            )
        await session.flush()


async def test_an_assignment_cannot_end_before_it_starts(pr_database: Database) -> None:
    """Requirement 7."""
    channel_id, _ = await _make_channel(pr_database, "CH-DATES")
    user_id = await _make_user(pr_database, "Backwards")
    with pytest.raises(IntegrityError):
        async with pr_database.transaction() as session:
            session.add(
                PrChannelAssignment(
                    channel_id=channel_id,
                    user_id=user_id,
                    assignment_role=PrChannelAssignmentRole.SEEDING_OWNER,
                    effective_from=date(2026, 3, 10),
                    effective_to=date(2026, 3, 9),
                )
            )
            await session.flush()


async def test_an_assignment_may_start_and_end_on_the_same_day(pr_database: Database) -> None:
    """One day is a real assignment - a cover for somebody on leave."""
    channel_id, _ = await _make_channel(pr_database, "CH-ONEDAY")
    user_id = await _make_user(pr_database, "Cover")
    async with pr_database.transaction() as session:
        session.add(
            PrChannelAssignment(
                channel_id=channel_id,
                user_id=user_id,
                assignment_role=PrChannelAssignmentRole.ANALYTICS_OWNER,
                effective_from=date(2026, 3, 10),
                effective_to=date(2026, 3, 10),
            )
        )
        await session.flush()


async def test_the_same_person_cannot_hold_one_channel_role_open_twice(
    pr_database: Database,
) -> None:
    """Requirement 8."""
    channel_id, _ = await _make_channel(pr_database, "CH-DUPASSIGN")
    user_id = await _make_user(pr_database, "Duplicated")

    async with pr_database.transaction() as session:
        session.add(
            PrChannelAssignment(
                channel_id=channel_id,
                user_id=user_id,
                assignment_role=PrChannelAssignmentRole.CHANNEL_OWNER,
                effective_from=date(2026, 1, 1),
            )
        )

    with pytest.raises(IntegrityError):
        async with pr_database.transaction() as session:
            session.add(
                PrChannelAssignment(
                    channel_id=channel_id,
                    user_id=user_id,
                    assignment_role=PrChannelAssignmentRole.CHANNEL_OWNER,
                    effective_from=date(2026, 6, 1),
                )
            )
            await session.flush()


async def test_overlapping_closed_assignment_periods_are_accepted(
    pr_database: Database,
) -> None:
    """The limitation, asserted so nobody mistakes the index for more.

    Two closed rows for the same channel, person and role whose date ranges
    overlap are **accepted** by this schema. The partial unique index only
    looks at open rows, and neither ``CHECK`` constraint looks at another row,
    so there is nothing here to reject them.

    Rejecting an overlap is a Step 1B service rule. This test fails the day
    somebody adds a database-level overlap constraint - at which point the
    deferred invariant in ``docs/pr/STEP_1A_PR_CORE_FOUNDATION.md`` is what
    needs updating, not this assertion.
    """
    channel_id, _ = await _make_channel(pr_database, "CH-OVERLAP")
    user_id = await _make_user(pr_database, "Overlapping owner")

    async with pr_database.transaction() as session:
        session.add(
            PrChannelAssignment(
                channel_id=channel_id,
                user_id=user_id,
                assignment_role=PrChannelAssignmentRole.CONTENT_OWNER,
                effective_from=date(2026, 1, 1),
                effective_to=date(2026, 6, 30),
            )
        )
        session.add(
            PrChannelAssignment(
                channel_id=channel_id,
                user_id=user_id,
                assignment_role=PrChannelAssignmentRole.CONTENT_OWNER,
                # Starts three months before the first row ends.
                effective_from=date(2026, 3, 1),
                effective_to=date(2026, 9, 30),
            )
        )
        await session.flush()

    async with pr_database.session() as session:
        rows = (
            await session.execute(
                text("SELECT count(*) FROM pr_channel_assignments WHERE channel_id = :id"),
                {"id": channel_id},
            )
        ).scalar_one()
    assert rows == 2, "the database is expected to accept overlapping closed periods"


async def test_a_closed_period_overlapping_an_open_one_is_accepted(
    pr_database: Database,
) -> None:
    """Same limitation, the mixed case.

    An open row starting in January and a closed row covering March to June are
    both stored. Only one is open, so the partial index has nothing to say
    about the pair even though they clearly overlap in time.
    """
    channel_id, _ = await _make_channel(pr_database, "CH-OVERLAP-MIXED")
    user_id = await _make_user(pr_database, "Mixed periods")

    async with pr_database.transaction() as session:
        session.add(
            PrChannelAssignment(
                channel_id=channel_id,
                user_id=user_id,
                assignment_role=PrChannelAssignmentRole.APPROVER,
                effective_from=date(2026, 1, 1),
            )
        )
        session.add(
            PrChannelAssignment(
                channel_id=channel_id,
                user_id=user_id,
                assignment_role=PrChannelAssignmentRole.APPROVER,
                effective_from=date(2026, 3, 1),
                effective_to=date(2026, 6, 30),
            )
        )
        await session.flush()

    async with pr_database.session() as session:
        rows = (
            await session.execute(
                text("SELECT count(*) FROM pr_channel_assignments WHERE channel_id = :id"),
                {"id": channel_id},
            )
        ).scalar_one()
    assert rows == 2


async def test_a_closed_assignment_does_not_block_a_new_one(pr_database: Database) -> None:
    """The other half of requirement 8: history is allowed to repeat.

    Somebody who owned a channel last year, handed it over, and has now taken
    it back has two rows for the same triple. Only one of them is open, which
    is exactly what the partial index permits.
    """
    channel_id, _ = await _make_channel(pr_database, "CH-REASSIGN")
    user_id = await _make_user(pr_database, "Returning owner")

    async with pr_database.transaction() as session:
        session.add(
            PrChannelAssignment(
                channel_id=channel_id,
                user_id=user_id,
                assignment_role=PrChannelAssignmentRole.CHANNEL_OWNER,
                effective_from=date(2025, 1, 1),
                effective_to=date(2025, 12, 31),
            )
        )
    async with pr_database.transaction() as session:
        session.add(
            PrChannelAssignment(
                channel_id=channel_id,
                user_id=user_id,
                assignment_role=PrChannelAssignmentRole.CHANNEL_OWNER,
                effective_from=date(2026, 1, 1),
            )
        )
        await session.flush()

    async with pr_database.session() as session:
        rows = (
            await session.execute(
                text("SELECT count(*) FROM pr_channel_assignments WHERE channel_id = :id"),
                {"id": channel_id},
            )
        ).scalar_one()
    assert rows == 2


async def test_a_channel_assignment_defaults_are_filled_by_the_database(
    pr_database: Database,
) -> None:
    """Insert without mentioning a defaulted column - the 0011 shape of test."""
    channel_id, _ = await _make_channel(pr_database, "CH-DEFAULTS")
    user_id = await _make_user(pr_database, "Defaulted")
    async with pr_database.transaction() as session:
        await session.execute(
            text(
                "INSERT INTO pr_channel_assignments "
                "(id, channel_id, user_id, assignment_role, effective_from) "
                "VALUES (:id, :channel, :user, 'APPROVER', :start)"
            ),
            {
                "id": uuid.uuid4(),
                "channel": channel_id,
                "user": user_id,
                "start": date(2026, 1, 1),
            },
        )
    async with pr_database.session() as session:
        row = (
            await session.execute(
                text(
                    "SELECT is_primary, allocation_percent, created_at, updated_at "
                    "FROM pr_channel_assignments WHERE channel_id = :id"
                ),
                {"id": channel_id},
            )
        ).one()
    is_primary, allocation, created_at, updated_at = row
    assert is_primary is False
    assert allocation == Decimal("100.00")
    assert created_at is not None
    assert updated_at is not None


# --- 9 & 10: one item, many channels, each once ----------------------------


async def test_one_content_item_targets_many_channels(pr_database: Database) -> None:
    """Requirement 9. This is why targets are a table and not a column."""
    channel_a, brand_id = await _make_channel(pr_database, "CH-FANOUT-A")
    channel_b, _ = await _make_channel(pr_database, "CH-FANOUT-B")
    channel_c, _ = await _make_channel(pr_database, "CH-FANOUT-C")
    content_id = await _make_content(pr_database, "CNT-2026-000010", brand_id)

    async with pr_database.transaction() as session:
        for offset, channel_id in enumerate((channel_a, channel_b, channel_c)):
            session.add(
                PrContentTarget(
                    content_id=content_id,
                    channel_id=channel_id,
                    target_publish_at=datetime(2026, 5, 1, 9, 0, tzinfo=UTC)
                    + timedelta(days=offset),
                )
            )
        await session.flush()

    async with pr_database.session() as session:
        count = (
            await session.execute(
                text("SELECT count(*) FROM pr_content_targets WHERE content_id = :id"),
                {"id": content_id},
            )
        ).scalar_one()
    assert count == 3


async def test_the_same_content_and_channel_cannot_be_targeted_twice(
    pr_database: Database,
) -> None:
    """Requirement 10 - what makes "publish this here" idempotent."""
    channel_id, brand_id = await _make_channel(pr_database, "CH-TARGETDUP")
    content_id = await _make_content(pr_database, "CNT-2026-000011", brand_id)

    async with pr_database.transaction() as session:
        session.add(PrContentTarget(content_id=content_id, channel_id=channel_id))

    with pytest.raises(IntegrityError):
        async with pr_database.transaction() as session:
            session.add(PrContentTarget(content_id=content_id, channel_id=channel_id))
            await session.flush()


# --- 11 & 12: one task, many assignees, each role once ---------------------


async def test_one_task_has_many_assignees(pr_database: Database) -> None:
    """Requirement 11. An owner, someone helping and someone reviewing."""
    task_id = await _make_task(pr_database, "TSK-2026-000010")
    now = datetime(2026, 4, 1, 8, 0, tzinfo=UTC)
    people = [
        (await _make_user(pr_database, "Owner"), PrTaskAssignmentRole.OWNER),
        (await _make_user(pr_database, "Helper"), PrTaskAssignmentRole.CONTRIBUTOR),
        (await _make_user(pr_database, "Reviewer"), PrTaskAssignmentRole.REVIEWER),
    ]
    async with pr_database.transaction() as session:
        for user_id, role in people:
            session.add(
                PrTaskAssignment(
                    task_id=task_id, user_id=user_id, assignment_role=role, assigned_at=now
                )
            )
        await session.flush()

    async with pr_database.session() as session:
        count = (
            await session.execute(
                text("SELECT count(*) FROM pr_task_assignments WHERE task_id = :id"),
                {"id": task_id},
            )
        ).scalar_one()
    assert count == 3


async def test_the_same_person_cannot_hold_one_task_role_twice(pr_database: Database) -> None:
    """Requirement 12."""
    task_id = await _make_task(pr_database, "TSK-2026-000011")
    user_id = await _make_user(pr_database, "Doubled")
    now = datetime(2026, 4, 1, 8, 0, tzinfo=UTC)

    async with pr_database.transaction() as session:
        session.add(
            PrTaskAssignment(
                task_id=task_id,
                user_id=user_id,
                assignment_role=PrTaskAssignmentRole.OWNER,
                assigned_at=now,
            )
        )

    with pytest.raises(IntegrityError):
        async with pr_database.transaction() as session:
            session.add(
                PrTaskAssignment(
                    task_id=task_id,
                    user_id=user_id,
                    assignment_role=PrTaskAssignmentRole.OWNER,
                    assigned_at=now,
                )
            )
            await session.flush()


async def test_the_same_person_may_hold_two_different_roles_on_one_task(
    pr_database: Database,
) -> None:
    """A small team really does have the writer reviewing somebody else's cut."""
    task_id = await _make_task(pr_database, "TSK-2026-000012")
    user_id = await _make_user(pr_database, "Wearing two hats")
    now = datetime(2026, 4, 1, 8, 0, tzinfo=UTC)
    async with pr_database.transaction() as session:
        session.add(
            PrTaskAssignment(
                task_id=task_id,
                user_id=user_id,
                assignment_role=PrTaskAssignmentRole.OWNER,
                assigned_at=now,
            )
        )
        session.add(
            PrTaskAssignment(
                task_id=task_id,
                user_id=user_id,
                assignment_role=PrTaskAssignmentRole.REVIEWER,
                assigned_at=now,
            )
        )
        await session.flush()


# --- 13: approval version --------------------------------------------------


@pytest.mark.parametrize("version", [0, -1])
async def test_an_approval_below_version_one_is_refused(
    pr_database: Database, version: int
) -> None:
    """Requirement 13.

    "Approved" is only ever an answer about a specific draft, and there is no
    draft zero.
    """
    _, brand_id = await _make_channel(pr_database, f"CH-APPROVAL-{version}")
    content_id = await _make_content(pr_database, f"CNT-2026-0000{20 - version}", brand_id)
    reviewer_id = await _make_user(pr_database, f"Reviewer {version}")
    with pytest.raises(IntegrityError):
        async with pr_database.transaction() as session:
            session.add(
                PrApprovalEvent(
                    content_id=content_id,
                    approval_stage=PrApprovalStage.HEAD_REVIEW,
                    reviewer_user_id=reviewer_id,
                    decision=PrApprovalDecision.APPROVED,
                    version_reviewed=version,
                    decided_at=datetime(2026, 4, 2, 10, 0, tzinfo=UTC),
                )
            )
            await session.flush()


async def test_a_reviewer_may_decide_the_same_item_twice_at_different_versions(
    pr_database: Database,
) -> None:
    """Append-only means both decisions are true, each at its own version."""
    _, brand_id = await _make_channel(pr_database, "CH-APPROVALS")
    content_id = await _make_content(pr_database, "CNT-2026-000030", brand_id)
    reviewer_id = await _make_user(pr_database, "Head")

    async with pr_database.transaction() as session:
        session.add(
            PrApprovalEvent(
                content_id=content_id,
                approval_stage=PrApprovalStage.HEAD_REVIEW,
                reviewer_user_id=reviewer_id,
                decision=PrApprovalDecision.REVISION_REQUIRED,
                version_reviewed=1,
                comment="Hook is weak.",
                decided_at=datetime(2026, 4, 2, 10, 0, tzinfo=UTC),
            )
        )
        session.add(
            PrApprovalEvent(
                content_id=content_id,
                approval_stage=PrApprovalStage.HEAD_REVIEW,
                reviewer_user_id=reviewer_id,
                decision=PrApprovalDecision.APPROVED,
                version_reviewed=2,
                decided_at=datetime(2026, 4, 3, 10, 0, tzinfo=UTC),
            )
        )
        await session.flush()

    async with pr_database.session() as session:
        decisions = [
            row[0]
            for row in (
                await session.execute(
                    text(
                        "SELECT decision FROM pr_approval_events "
                        "WHERE content_id = :id ORDER BY decided_at"
                    ),
                    {"id": content_id},
                )
            ).all()
        ]
    assert decisions == ["REVISION_REQUIRED", "APPROVED"]


async def test_the_approval_table_has_no_updated_at_column(pr_database: Database) -> None:
    """Requirement 17, read from the live catalog rather than the model."""
    async with pr_database.session() as session:
        columns = {
            row[0]
            for row in (
                await session.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND table_name = 'pr_approval_events'"
                    )
                )
            ).all()
        }
    assert "updated_at" not in columns
    assert {"created_at", "decided_at", "version_reviewed"} <= columns


# --- 14 & 16: identity and keys, in the migrated catalog -------------------


async def test_every_pr_person_column_references_the_users_table(
    pr_database: Database,
) -> None:
    """Requirement 14, read from the live catalog.

    The offline test asserts the same thing about the models. This one asserts
    it about the schema Alembic actually built, which is the only place the two
    could have disagreed.
    """
    async with pr_database.session() as session:
        rows = (
            await session.execute(
                text(
                    """
                    SELECT tc.table_name, kcu.column_name, ccu.table_name AS referenced,
                           rc.delete_rule
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                      ON tc.constraint_name = kcu.constraint_name
                    JOIN information_schema.constraint_column_usage ccu
                      ON tc.constraint_name = ccu.constraint_name
                    JOIN information_schema.referential_constraints rc
                      ON tc.constraint_name = rc.constraint_name
                    WHERE tc.constraint_type = 'FOREIGN KEY'
                      AND tc.table_name LIKE 'pr\\_%'
                    """
                )
            )
        ).all()

    person_references = {
        (table, column, referenced, rule)
        for table, column, referenced, rule in rows
        if column.endswith("user_id")
    }
    assert person_references, "no PR foreign key names a person"
    assert {referenced for _, _, referenced, _ in person_references} == {"users"}
    assert {rule for _, _, _, rule in person_references} == {"RESTRICT"}

    # Requirement 16: no relationship is keyed on a URL.
    assert not [row for row in rows if "url" in row[1]]

    # Nothing in this module cascades **towards a business row**. The two
    # exceptions Step 1F.2.7 added are the child tables that spell out one
    # approval grant's scope: those rows are parts of the grant, not facts of
    # their own, and their other foreign key - the one that names a channel -
    # is ``RESTRICT`` like everything else, so a channel a grant covers still
    # cannot be deleted out from under it.
    cascading = {(table, column) for table, column, _, rule in rows if rule == "CASCADE"}
    assert cascading == {
        ("pr_user_capability_content_types", "capability_grant_id"),
        ("pr_user_capability_channels", "capability_grant_id"),
        # M6 adds **no** cascade. Its one-time exception - a bonus allocation
        # belonging to a pool - went with the compensation engine, and its
        # provenance row deliberately never cascaded: a contribution that has
        # been scored must not be deletable, because that row is what makes a
        # month's total explainable.
        #
        # M4B adds exactly one, and it is the same shape as the two above: the
        # people a recurring template names are **parts of the template**, not
        # facts of their own, and a template that may be deleted at all is one
        # the scheduler has never reached. Its other foreign key - the one that
        # names a person - is ``RESTRICT`` like everything else, and so is the
        # occurrence ledger's link to the template, which is what actually stops
        # a routine that has run from being deleted.
        ("pr_work_recurring_template_contributors", "template_id"),
    }
    assert {rule for _, _, _, rule in rows} <= {"RESTRICT", "CASCADE"}


async def test_deleting_a_user_who_owns_pr_work_is_refused(pr_database: Database) -> None:
    """``RESTRICT`` in practice, not just in the catalog."""
    _, brand_id = await _make_channel(pr_database, "CH-RESTRICT")
    content_id = await _make_content(pr_database, "CNT-2026-000040", brand_id)
    async with pr_database.session() as session:
        owner_id = (
            await session.execute(
                text("SELECT owner_user_id FROM pr_content_items WHERE id = :id"),
                {"id": content_id},
            )
        ).scalar_one()

    with pytest.raises(IntegrityError):
        async with pr_database.transaction() as session:
            await session.execute(text("DELETE FROM users WHERE id = :id"), {"id": owner_id})


async def test_deleting_a_brand_that_has_content_is_refused(pr_database: Database) -> None:
    """History does not disappear as a side effect of tidying up."""
    _, brand_id = await _make_channel(pr_database, "CH-BRANDRESTRICT")
    await _make_content(pr_database, "CNT-2026-000041", brand_id)
    with pytest.raises(IntegrityError):
        async with pr_database.transaction() as session:
            await session.execute(text("DELETE FROM pr_brands WHERE id = :id"), {"id": brand_id})


async def test_deleting_a_content_item_that_has_approvals_is_refused(
    pr_database: Database,
) -> None:
    """Requirement: approval history must not vanish automatically."""
    _, brand_id = await _make_channel(pr_database, "CH-APPRESTRICT")
    content_id = await _make_content(pr_database, "CNT-2026-000042", brand_id)
    reviewer_id = await _make_user(pr_database, "Team lead")
    async with pr_database.transaction() as session:
        session.add(
            PrApprovalEvent(
                content_id=content_id,
                approval_stage=PrApprovalStage.TEAM_LEAD_REVIEW,
                reviewer_user_id=reviewer_id,
                decision=PrApprovalDecision.APPROVED,
                version_reviewed=1,
                decided_at=datetime(2026, 4, 4, 10, 0, tzinfo=UTC),
            )
        )

    with pytest.raises(IntegrityError):
        async with pr_database.transaction() as session:
            await session.execute(
                text("DELETE FROM pr_content_items WHERE id = :id"), {"id": content_id}
            )


# --- 15: no second identity table ------------------------------------------


async def test_the_migrated_schema_has_no_second_identity_table(
    pr_database: Database,
) -> None:
    """Requirement 15, read from the live catalog."""
    async with pr_database.session() as session:
        tables = {
            row[0]
            for row in (
                await session.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'public'"
                    )
                )
            ).all()
        }
    # Whole words, matching ``IDENTITY_TABLE_SMELL`` in the offline test. A
    # plain substring check flags ``pr_user_capabilities`` - which is a *grant*
    # table keyed on ``users.id``, the opposite of a second identity table -
    # and a guard that cries wolf gets deleted rather than heeded.
    smell = re.compile(r"staff|employee|personnel|people|member_profile|pr_users?\b")
    offenders = {name for name in tables if smell.search(name)}
    assert offenders == set()
    assert "users" in tables


# --- Defaults, end to end --------------------------------------------------


async def test_a_content_item_and_a_task_take_their_defaults_from_the_database(
    pr_database: Database,
) -> None:
    """Insert without naming priority, stage, status or a timestamp."""
    _, brand_id = await _make_channel(pr_database, "CH-ROWDEFAULTS")
    user_id = await _make_user(pr_database, "Defaulting author")
    content_id, task_id = uuid.uuid4(), uuid.uuid4()

    async with pr_database.transaction() as session:
        await session.execute(
            text(
                "INSERT INTO pr_content_items "
                "(id, code, title, brand_id, owner_user_id, created_by_user_id) "
                "VALUES (:id, 'CNT-2026-000050', 'Defaults', :brand, :user, :user)"
            ),
            {"id": content_id, "brand": brand_id, "user": user_id},
        )
        await session.execute(
            text(
                "INSERT INTO pr_tasks (id, code, task_type, title, created_by_user_id) "
                "VALUES (:id, 'TSK-2026-000050', 'SCRIPT', 'Defaults', :user)"
            ),
            {"id": task_id, "user": user_id},
        )

    async with pr_database.session() as session:
        priority, stage, created_at, updated_at = (
            await session.execute(
                text(
                    "SELECT priority, workflow_stage, created_at, updated_at "
                    "FROM pr_content_items WHERE id = :id"
                ),
                {"id": content_id},
            )
        ).one()
        task_priority, task_status, task_completed = (
            await session.execute(
                text("SELECT priority, status, completed_at FROM pr_tasks WHERE id = :id"),
                {"id": task_id},
            )
        ).one()

    assert (priority, stage) == ("NORMAL", "IDEA")
    assert created_at is not None and updated_at is not None
    assert (task_priority, task_status) == ("NORMAL", "TODO")
    # Nothing in the database sets a completion time.
    assert task_completed is None
