"""Step 1C.1 on a real PostgreSQL: the migration, and the allocator under load.

Concurrency is the only reason this file exists. The offline suite proves that
``CH-0001`` is formatted correctly and that a year rolls over; it cannot prove
the thing the allocator was written for, because SQLite serialises writers and
:class:`~meobot.application.pr_code_service.PrCodeService` deliberately takes a
different, simpler path there.

What is settled here:

* migration 0016 applies and reverses, and the models match what it built;
* two transactions allocating at the same instant get different numbers;
* the same holds when they are creating whole entities, not just numbers;
* year namespaces are genuinely separate rows and separate sequences.

Run it against a PostgreSQL you are willing to have scratch databases created
in and dropped from::

    export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/meobot_test
    uv run pytest tests/integration/test_pr_code_allocation.py -m integration

Each fixture creates its own uniquely-named ``meobot_pr1c1_*`` database and
drops that one afterwards.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from meobot.application.audit_service import AuditService
from meobot.application.pr_capability_service import PrCapabilityService
from meobot.application.pr_code_service import PrCodeService
from meobot.application.pr_content_service import CreateContentCommand, PrContentService
from meobot.application.pr_task_service import CreateTaskCommand, PrTaskService
from meobot.core.config import Settings, get_settings
from meobot.db.base import Base
from meobot.db.models.pr import PrBrand
from meobot.db.models.user import User
from meobot.db.session import Database
from meobot.domain.identity.models import Actor, Role
from meobot.domain.pr.policy import PrCapability
from tests.integration.legacy_rows import insert_legacy_user
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

NEW_TABLES = {"pr_user_capabilities", "pr_code_counters"}
NOW = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)

#: How many transactions race in the concurrency tests. Small enough to run in
#: seconds, large enough that a read-then-write allocator would collide with
#: near-certainty rather than occasionally.
RACERS = 12


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
        dsn = base.set(database=scratch).render_as_string(hide_password=False)
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


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def pr_database() -> AsyncIterator[Database]:
    async for database in _scratch_database("meobot_pr1c1"):
        yield database


@pytest_asyncio.fixture(loop_scope="module")
async def fresh_database() -> AsyncIterator[Database]:
    async for database in _scratch_database("meobot_pr1c1mig"):
        yield database


# --- Migration -------------------------------------------------------------


async def test_the_chain_reaches_0016_with_both_new_tables(pr_database: Database) -> None:
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
    # The fixture upgrades to ``head``, so that is what the stamp must be - read
    # from the script directory rather than listed here. An enumerated set was
    # the previous shape and it was really asserting "these are the newest
    # migrations", a fact about the calendar that needed an edit per release and
    # did not get one.
    assert stamped == alembic_head(), stamped
    assert present >= NEW_TABLES, "0016's two tables must exist at or above 0016."
    assert present >= NEW_TABLES
    # Everything Step 1C.1 builds on is still there.
    assert {"pr_content_items", "pr_approval_events", "pr_content_versions"} <= present


async def test_downgrading_0016_removes_only_the_two_new_tables(
    fresh_database: Database,
) -> None:
    dsn = str(fresh_database._settings.database_url)

    async def table_names() -> set[str]:
        async with fresh_database.session() as session:
            rows = await session.execute(
                text(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
                )
            )
            return {row[0] for row in rows.all()}

    # Stand at 0016 **before** taking the baseline, so what follows measures
    # 0016's own downgrade rather than the combined effect of every revision
    # above 0015. Since Step 1E that difference is one table (``web_sessions``),
    # and counting it here would fail this test for a reason with nothing to do
    # with codes.
    await downgrade_to(dsn, "0016")

    before = await table_names()
    assert before >= NEW_TABLES

    await downgrade_to(dsn, "0015")
    after = await table_names()
    assert before - after == NEW_TABLES, "the downgrade removed something else"
    assert {"users", "pr_content_items", "pr_content_versions"} <= after

    await upgrade_to(dsn, "head")
    assert await table_names() >= NEW_TABLES


async def test_the_models_and_the_migration_describe_the_same_schema(
    pr_database: Database,
) -> None:
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


async def test_the_partial_unique_indexes_exist_and_are_partial(
    pr_database: Database,
) -> None:
    """The pair that makes ``ON CONFLICT`` work with a nullable ``year``."""
    async with pr_database.session() as session:
        definitions = dict(
            (
                await session.execute(
                    text(
                        "SELECT indexname, indexdef FROM pg_indexes "
                        "WHERE schemaname = 'public' AND tablename = 'pr_code_counters'"
                    )
                )
            ).all()
        )
    yearly = definitions["uq_pr_code_counters_namespace_year"]
    global_ = definitions["uq_pr_code_counters_namespace_global"]
    assert "UNIQUE" in yearly and "year IS NOT NULL" in yearly
    assert "UNIQUE" in global_ and "year IS NULL" in global_


async def test_a_second_global_counter_row_is_refused(pr_database: Database) -> None:
    """Without the partial index this would be accepted and reset the sequence."""
    async with pr_database.transaction() as session:
        await session.execute(
            text(
                "INSERT INTO pr_code_counters (id, namespace, year, next_value) "
                "VALUES (:id, 'DUPTEST', NULL, 1)"
            ),
            {"id": uuid.uuid4()},
        )
    with pytest.raises(IntegrityError):
        async with pr_database.transaction() as session:
            await session.execute(
                text(
                    "INSERT INTO pr_code_counters (id, namespace, year, next_value) "
                    "VALUES (:id, 'DUPTEST', NULL, 1)"
                ),
                {"id": uuid.uuid4()},
            )


# --- Sequential behaviour, on the production code path ---------------------


async def test_the_upsert_path_starts_at_one_and_increments(pr_database: Database) -> None:
    """The same expectations as offline, but through ``ON CONFLICT``."""
    async with pr_database.transaction() as session:
        codes = PrCodeService(session, get_settings())
        assert await codes.allocate_channel_code() == "CH-0001"
        assert await codes.allocate_channel_code() == "CH-0002"
        assert await codes.allocate_content_code(at=NOW) == "CNT-2026-000001"
        assert await codes.allocate_content_code(at=NOW) == "CNT-2026-000002"


async def test_year_namespaces_are_separate_sequences(pr_database: Database) -> None:
    """2026 and 2027 are two rows, and neither disturbs the other."""
    year_2027 = datetime(2027, 6, 1, 9, 0, tzinfo=UTC)
    async with pr_database.transaction() as session:
        codes = PrCodeService(session, get_settings())
        assert await codes.allocate_task_code(at=NOW) == "TSK-2026-000001"
        assert await codes.allocate_task_code(at=NOW) == "TSK-2026-000002"
        assert await codes.allocate_task_code(at=year_2027) == "TSK-2027-000001"
        assert await codes.allocate_task_code(at=NOW) == "TSK-2026-000003"
        assert await codes.allocate_task_code(at=year_2027) == "TSK-2027-000002"

    async with pr_database.session() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT year, next_value FROM pr_code_counters "
                    "WHERE namespace = 'TASK' ORDER BY year"
                )
            )
        ).all()
    assert [(year, value) for year, value in rows] == [(2026, 4), (2027, 3)]


async def test_channel_numbering_has_one_row_and_no_year(pr_database: Database) -> None:
    """One counter for channels, forever, with no year in it.

    Written as a delta rather than an absolute because this database is shared
    by the whole module: what matters is that three allocations move one
    year-less row by three, not that it happens to reach four.
    """

    async def counter() -> tuple[int, int]:
        async with pr_database.session() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT year, next_value FROM pr_code_counters WHERE namespace = 'CHANNEL'"
                    )
                )
            ).all()
        assert len(rows) == 1, rows
        assert rows[0][0] is None
        return rows[0]

    async with pr_database.transaction() as session:
        codes = PrCodeService(session, get_settings())
        await codes.allocate_channel_code()
    _, before = await counter()

    async with pr_database.transaction() as session:
        codes = PrCodeService(session, get_settings())
        for _ in range(3):
            await codes.allocate_channel_code()
    _, after = await counter()
    assert after == before + 3


# --- Concurrency, which is the point --------------------------------------


async def test_concurrent_channel_allocations_are_all_different(
    pr_database: Database,
) -> None:
    """Requirement 4. Twelve transactions, twelve distinct numbers."""

    async def allocate() -> str:
        async with pr_database.transaction() as session:
            return await PrCodeService(session, get_settings()).allocate_channel_code()

    codes = await asyncio.gather(*(allocate() for _ in range(RACERS)))
    assert len(set(codes)) == RACERS, sorted(codes)
    assert all(code.startswith("CH-") for code in codes)


async def test_concurrent_content_allocations_are_all_different(
    pr_database: Database,
) -> None:
    async def allocate() -> str:
        async with pr_database.transaction() as session:
            return await PrCodeService(session, get_settings()).allocate_content_code(at=NOW)

    codes = await asyncio.gather(*(allocate() for _ in range(RACERS)))
    assert len(set(codes)) == RACERS, sorted(codes)


async def _seed_author(database: Database) -> tuple[Actor, uuid.UUID, uuid.UUID]:
    """An owner who may create content and tasks, plus a brand to hang it on."""
    async with database.transaction() as session:
        owner = User(full_name="Racer", role=Role.OWNER)
        brand = PrBrand(code=f"BRND-{uuid.uuid4().hex[:8]}", name="Racing brand")
        session.add_all([owner, brand])
        await session.flush()
        owner_id, brand_id = owner.id, brand.id
    return Actor(user_id=owner_id, full_name="Racer", role=Role.OWNER), owner_id, brand_id


async def test_concurrent_content_creations_get_different_codes(
    pr_database: Database,
) -> None:
    """Requirements 1 and 3, through the whole command rather than the allocator.

    This is the shape the defect would actually have taken: not "two calls to a
    counter", but two people pressing create at the same moment, each inside a
    transaction that also writes a content item, a version and an audit row.
    """
    actor, owner_id, brand_id = await _seed_author(pr_database)

    async def create(index: int) -> str:
        async with pr_database.transaction() as session:
            audit = AuditService(session)
            service = PrContentService(
                session,
                audit,
                PrCapabilityService(session, audit),
                PrCodeService(session, get_settings()),
            )
            snapshot = await service.create_content(
                actor=actor,
                request_id=uuid.uuid4(),
                command=CreateContentCommand(
                    title=f"Racing draft {index}",
                    brand_id=brand_id,
                    owner_user_id=owner_id,
                ),
            )
            return snapshot.content.code

    codes = await asyncio.gather(*(create(index) for index in range(RACERS)))
    assert len(set(codes)) == RACERS, sorted(codes)

    async with pr_database.session() as session:
        stored = (
            await session.execute(
                text("SELECT count(DISTINCT code), count(*) FROM pr_content_items")
            )
        ).one()
    assert stored[0] == stored[1]


async def test_concurrent_task_creations_get_different_codes(pr_database: Database) -> None:
    """Requirement 2."""
    actor, _, _ = await _seed_author(pr_database)

    async def create(index: int) -> str:
        async with pr_database.transaction() as session:
            audit = AuditService(session)
            service = PrTaskService(
                session,
                audit,
                PrCapabilityService(session, audit),
                PrCodeService(session, get_settings()),
            )
            task = await service.create_task(
                actor=actor,
                request_id=uuid.uuid4(),
                command=CreateTaskCommand(task_type="SCRIPT", title=f"Racing task {index}"),
            )
            return task.code

    codes = await asyncio.gather(*(create(index) for index in range(RACERS)))
    assert len(set(codes)) == RACERS, sorted(codes)


async def test_a_rolled_back_command_hands_its_number_back(pr_database: Database) -> None:
    """The consequence of using a table row rather than a sequence.

    ``nextval()`` would not roll back - that is what makes sequences gappy.
    An ``UPDATE`` does, so a command that takes a number and then fails leaves
    the counter exactly where it was and the next creation reuses the number.

    Asserted because the alternative was documented as acceptable before it
    was measured, and it turned out not to be what happens.
    """
    async with pr_database.transaction() as session:
        first = await PrCodeService(session, get_settings()).allocate_content_code(at=NOW)

    with pytest.raises(RuntimeError):
        async with pr_database.transaction() as session:
            await PrCodeService(session, get_settings()).allocate_content_code(at=NOW)
            raise RuntimeError("business command failed after taking a number")

    async with pr_database.transaction() as session:
        third = await PrCodeService(session, get_settings()).allocate_content_code(at=NOW)

    first_number = int(first.rsplit("-", 1)[1])
    third_number = int(third.rsplit("-", 1)[1])
    assert third_number == first_number + 1, (first, third)


# --- Grants, on a real database -------------------------------------------


async def test_0031_preserves_an_existing_grant_exactly(fresh_database: Database) -> None:
    """The migration's one data statement, run against a real pre-0031 schema.

    A grant written under the old rule is stood back up at 0030, the revision is
    applied, and what comes out must be the *same authority*: ``ALL``/``ALL``,
    because an unscoped grant applied to everything, and
    ``requires_role_baseline``, because the holder's role is what made them
    entitled. Reading it as a standalone grant would hand somebody approval
    rights nobody decided to give, which is the one thing this revision must not
    do quietly.
    """
    dsn = str(fresh_database._settings.database_url)
    await downgrade_to(dsn, "0030")

    async with fresh_database.transaction() as session:
        person_id = await insert_legacy_user(session, full_name="Legacy grantee", role="ADMIN")
        await session.execute(
            text(
                "INSERT INTO pr_user_capabilities (id, user_id, capability, note) "
                "VALUES (:id, :user, :capability, 'granted before scoping')"
            ),
            {
                "id": uuid.uuid4(),
                "user": person_id,
                "capability": PrCapability.PR_HEAD_REVIEW.value,
            },
        )

    await upgrade_to(dsn, "head")

    async with fresh_database.session() as session:
        row = (
            await session.execute(
                text(
                    "SELECT content_type_scope, channel_scope, include_unclassified_content, "
                    "       include_unassigned_channel, requires_role_baseline, revoked_at, note "
                    "  FROM pr_user_capabilities WHERE user_id = :user"
                ),
                {"user": person_id},
            )
        ).one()
    assert row.content_type_scope == "ALL"
    assert row.channel_scope == "ALL"
    assert row.include_unclassified_content is True
    assert row.include_unassigned_channel is True
    # The load-bearing one: the old row keeps the old, conjunctive rule.
    assert row.requires_role_baseline is True
    # Nothing was revoked, and the note somebody wrote is untouched.
    assert row.revoked_at is None
    assert row.note == "granted before scoping"


async def test_0031_leaves_new_grants_fail_closed(fresh_database: Database) -> None:
    """A row written *after* the revision, with no scope, covers nothing.

    The server defaults, asserted where they actually take effect. ``SELECTED``
    with nothing selected and both include flags ``false`` is the shape a bug
    produces, and it refuses rather than admits.
    """
    async with fresh_database.transaction() as session:
        person_id = await insert_legacy_user(session, full_name="Fresh grantee", role="ADMIN")
        await session.execute(
            text(
                "INSERT INTO pr_user_capabilities (id, user_id, capability) "
                "VALUES (:id, :user, :capability)"
            ),
            {
                "id": uuid.uuid4(),
                "user": person_id,
                "capability": PrCapability.PR_HEAD_REVIEW.value,
            },
        )

    async with fresh_database.session() as session:
        row = (
            await session.execute(
                text(
                    "SELECT content_type_scope, channel_scope, include_unclassified_content, "
                    "       include_unassigned_channel, requires_role_baseline "
                    "  FROM pr_user_capabilities WHERE user_id = :user"
                ),
                {"user": person_id},
            )
        ).one()
    assert row.content_type_scope == "SELECTED"
    assert row.channel_scope == "SELECTED"
    assert row.include_unclassified_content is False
    assert row.include_unassigned_channel is False
    assert row.requires_role_baseline is False


async def test_two_scoped_grants_of_one_gate_coexist_in_the_database(
    pr_database: Database,
) -> None:
    """Step 1F.2.7 dropped the partial unique index, and this is the proof.

    Until this step the database itself refused a second open grant of one
    capability to one person. That was right when a grant had no scope and two
    of them could only be a duplicate; it is wrong now, because *Facebook posts
    on the Facebook channels* and *short-video scripts everywhere* are two
    different rights, and a schema that called them a conflict would force one
    grant per combination.

    What replaces it is a plain lookup index here and a refusal of an
    **identical** scope in ``PrCapabilityService.grant`` - where the comparison
    can actually be made. That refusal is asserted in
    ``tests/unit/test_pr_scoped_approval_grants.py``; what this asserts is that
    the database no longer stands in its way.
    """
    async with pr_database.transaction() as session:
        person = User(full_name="Grantee", role=Role.ADMIN)
        session.add(person)
        await session.flush()
        person_id = person.id

    for scope in ("SELECTED", "ALL"):
        async with pr_database.transaction() as session:
            await session.execute(
                text(
                    "INSERT INTO pr_user_capabilities "
                    "(id, user_id, capability, channel_scope) "
                    "VALUES (:id, :user, :capability, :scope)"
                ),
                {
                    "id": uuid.uuid4(),
                    "user": person_id,
                    "capability": PrCapability.PR_HEAD_REVIEW.value,
                    "scope": scope,
                },
            )

    async with pr_database.session() as session:
        held = await session.execute(
            text(
                "SELECT count(*) FROM pr_user_capabilities "
                "WHERE user_id = :user AND effective_to IS NULL AND revoked_at IS NULL"
            ),
            {"user": person_id},
        )
        assert held.scalar_one() == 2

    # A closed row alongside the open ones is still fine - history repeating.
    async with pr_database.transaction() as session:
        await session.execute(
            text(
                "INSERT INTO pr_user_capabilities "
                "(id, user_id, capability, effective_from, effective_to) "
                "VALUES (:id, :user, :capability, '2024-01-01', '2024-12-31')"
            ),
            {
                "id": uuid.uuid4(),
                "user": person_id,
                "capability": PrCapability.PR_HEAD_REVIEW.value,
            },
        )


async def test_a_grant_scope_cannot_name_one_value_twice(pr_database: Database) -> None:
    """The unique index that *did* survive, one per child table.

    A grant listing ``FACEBOOK_POST`` twice is a data-entry accident that would
    make "how many classifications does this cover" answer wrongly, and it is
    the database's job to refuse it - the sets are small and the constraint is
    exact.
    """
    async with pr_database.transaction() as session:
        person = User(full_name="Scoped", role=Role.ADMIN)
        session.add(person)
        await session.flush()
        person_id = person.id

    grant_id = uuid.uuid4()
    async with pr_database.transaction() as session:
        await session.execute(
            text(
                "INSERT INTO pr_user_capabilities (id, user_id, capability) "
                "VALUES (:id, :user, :capability)"
            ),
            {
                "id": grant_id,
                "user": person_id,
                "capability": PrCapability.PR_HEAD_REVIEW.value,
            },
        )
        await session.execute(
            text(
                "INSERT INTO pr_user_capability_content_types "
                "(id, capability_grant_id, content_type) VALUES (:id, :grant, 'FACEBOOK_POST')"
            ),
            {"id": uuid.uuid4(), "grant": grant_id},
        )

    with pytest.raises(IntegrityError):
        async with pr_database.transaction() as session:
            await session.execute(
                text(
                    "INSERT INTO pr_user_capability_content_types "
                    "(id, capability_grant_id, content_type) "
                    "VALUES (:id, :grant, 'FACEBOOK_POST')"
                ),
                {"id": uuid.uuid4(), "grant": grant_id},
            )


async def test_deleting_a_user_who_holds_a_grant_is_refused(pr_database: Database) -> None:
    async with pr_database.transaction() as session:
        person = User(full_name="Restricted", role=Role.ADMIN)
        session.add(person)
        await session.flush()
        person_id = person.id
        session.add_all([])
    async with pr_database.transaction() as session:
        await session.execute(
            text(
                "INSERT INTO pr_user_capabilities (id, user_id, capability) "
                "VALUES (:id, :user, :capability)"
            ),
            {
                "id": uuid.uuid4(),
                "user": person_id,
                "capability": PrCapability.PR_TEAM_LEAD_REVIEW.value,
            },
        )

    with pytest.raises(IntegrityError):
        async with pr_database.transaction() as session:
            await session.execute(text("DELETE FROM users WHERE id = :id"), {"id": person_id})
