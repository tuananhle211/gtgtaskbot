"""``0037`` against a real PostgreSQL: the roundtrip, the drift check, the backfill.

What only a real database answers here:

* **the models describe the migration.** ``compare_metadata`` over the two
  columns ``0037`` adds, which is the exit-gate item that stops the ORM and the
  revision drifting apart;
* **the migration goes both ways.** ``0037 -> 0036 -> 0037``, because a
  downgrade nobody has run is a downgrade that does not work - and because this
  one drops a foreign key, which is the kind of thing that fails only on the
  database that enforces them;
* **the backfill recovers rather than guesses.** It is the one migration in
  this repository that touches existing rows, and the claim that earns it that
  licence is checked here against rows written under ``0036``: content work is
  given the milestone instant from the column nothing rewrites, recurring work
  is **joined to its occurrence** through a one-time decode of the source key,
  and a row whose occurrence cannot be found is left null rather than handed a
  plausible-looking wrong date;
* **manual work is left alone.** The column is null for it in the schema and
  stays null through the backfill, which is the whole "do not invent a date"
  rule expressed as data rather than as a docstring.

Requires a PostgreSQL you are willing to have databases created on::

    export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/postgres
    uv run pytest tests/integration/test_pr_work_execution_at_pg.py -m integration
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

import meobot.db.models  # noqa: F401 - registers every model for compare_metadata
from meobot.core.config import Settings
from meobot.db.base import Base
from meobot.db.session import Database
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

SETTINGS_KWARGS = {"_env_file": None, "web_cookie_secure": False}

#: Everything ``0037`` adds, and the filter every drift assertion uses.
NEW_COLUMNS = ("execution_at", "recurring_occurrence_id")

MILESTONE = datetime(2026, 9, 4, 2, 0, tzinfo=UTC)
#: A content item sent back and finished again: same milestone, later ``completed_at``.
REOPENED_AND_FINISHED = datetime(2026, 9, 9, 8, 0, tzinfo=UTC)
#: Two firings, and the instant a catch-up sweep actually wrote their rows.
FIRING = datetime(2026, 9, 2, 2, 0, tzinfo=UTC)
LATER_FIRING = datetime(2026, 9, 3, 2, 0, tzinfo=UTC)
GENERATED_LATE = datetime(2026, 9, 4, 5, 30, tzinfo=UTC)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def dsn() -> AsyncIterator[str]:
    """A scratch database migrated to head."""
    assert TEST_DATABASE_URL is not None
    base = make_url(TEST_DATABASE_URL)
    scratch = f"meobot_0037_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(
        base.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=None
    )
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{scratch}"'))
        url = base.set(database=scratch).render_as_string(hide_password=False)
        await upgrade_to(url)
        yield url
    finally:
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)'))
        await admin.dispose()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def database(dsn: str) -> AsyncIterator[Database]:
    handle = Database(Settings(database_url=dsn, **SETTINGS_KWARGS))
    try:
        yield handle
    finally:
        await handle.dispose()


async def _seed(  # type: ignore[no-untyped-def]
    connection,
    *,
    source: str,
    code: str,
    source_key: str | None = None,
    **stamps: datetime | None,
) -> uuid.UUID:
    """One work item written with raw SQL, as a row from before ``0037``."""
    user_id = await connection.scalar(
        text(
            "INSERT INTO users (id, full_name, role, active, created_at, updated_at) "
            "VALUES (gen_random_uuid(), 'Người cũ', 'EMPLOYEE', true, now(), now()) "
            "ON CONFLICT DO NOTHING RETURNING id"
        )
    )
    if user_id is None:
        user_id = await connection.scalar(text("SELECT id FROM users LIMIT 1"))
    work_type_id = await connection.scalar(text("SELECT id FROM pr_work_types LIMIT 1"))
    if work_type_id is None:
        work_type_id = await connection.scalar(
            text(
                "INSERT INTO pr_work_types "
                "(id, code, name, category, default_unit, default_quota_basis, "
                " requires_evidence, is_active, display_order, created_at, updated_at) "
                "VALUES (gen_random_uuid(), 'LEGACY', 'Việc cũ', 'OPERATIONS', 'ITEM', "
                " 'ITEM_COUNT', false, true, 0, now(), now()) RETURNING id"
            )
        )
    return await connection.scalar(
        text(
            "INSERT INTO pr_work_items "
            "(id, code, title, work_type_id, source_type, source_key, status, priority, "
            " created_by_user_id, accepted_at, assigned_at, completed_at, "
            " created_at, updated_at) "
            "VALUES (gen_random_uuid(), :code, 'Việc cũ', :work_type_id, :source, :key, "
            " 'COMPLETED', 'NORMAL', :user_id, :accepted_at, :assigned_at, :completed_at, "
            " now(), now()) "
            "RETURNING id"
        ),
        {
            "code": code,
            "work_type_id": work_type_id,
            "source": source,
            "key": (
                None
                if source == "MANUAL"
                else source_key or f"{source.lower()}:{uuid.uuid4()}:LEGACY"
            ),
            "user_id": user_id,
            "accepted_at": stamps.get("accepted_at"),
            "assigned_at": stamps.get("assigned_at"),
            "completed_at": stamps.get("completed_at"),
        },
    )


# ===========================================================================
# The models describe the migration
# ===========================================================================


async def test_01_models_and_migration_agree(database: Database) -> None:
    """**The exit-gate check.** Zero drift over what ``0037`` adds.

    Filtered to the two new column names because the repository has
    pre-existing drift between older models and older migrations - widening the
    assertion would make it fail for reasons this patch did not cause and must
    not fix.
    """
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    def _compare(sync_connection):  # type: ignore[no-untyped-def]
        context = MigrationContext.configure(
            sync_connection, opts={"compare_type": True, "compare_server_default": True}
        )
        return list(compare_metadata(context, Base.metadata))

    async with database.engine.connect() as connection:
        differences = await connection.run_sync(_compare)

    ours = [
        difference
        for difference in differences
        if any(name in str(difference) for name in NEW_COLUMNS)
    ]
    assert ours == [], ours


async def test_02_the_head_is_the_newest_revision_on_disk(database: Database) -> None:
    async with database.session() as session:
        head = await session.scalar(text("SELECT version_num FROM alembic_version"))
    assert head == alembic_head()


async def test_03_both_columns_exist_with_their_indexes(database: Database) -> None:
    """A nullable timestamp and a nullable foreign key, each indexed.

    The indexes are not decoration: the monthly list orders by ``execution_at``
    and the work card resolves its template through the occurrence id, and both
    run on every page of a management screen.
    """
    async with database.session() as session:
        columns = dict(
            (
                await session.execute(
                    text(
                        "SELECT column_name, is_nullable FROM information_schema.columns "
                        "WHERE table_name = 'pr_work_items' AND column_name = ANY(:names)"
                    ),
                    {"names": list(NEW_COLUMNS)},
                )
            ).all()
        )
        assert columns == {"execution_at": "YES", "recurring_occurrence_id": "YES"}

        indexes = (
            (
                await session.execute(
                    text("SELECT indexname FROM pg_indexes WHERE tablename = 'pr_work_items'")
                )
            )
            .scalars()
            .all()
        )
    assert "ix_pr_work_items_execution_at" in indexes
    assert "ix_pr_work_items_recurring_occurrence_id" in indexes


async def test_04_the_occurrence_link_is_a_real_foreign_key(database: Database) -> None:
    """``RESTRICT`` towards the occurrence ledger, enforced by the database.

    The column exists so that nothing has to decode a source key. A plain uuid
    with no constraint would have been the same string-handling problem wearing
    a different type.
    """
    async with database.session() as session:
        rule = await session.scalar(
            text(
                "SELECT rc.delete_rule FROM information_schema.referential_constraints rc "
                "JOIN information_schema.key_column_usage k "
                "  ON k.constraint_name = rc.constraint_name "
                "WHERE k.table_name = 'pr_work_items' "
                "  AND k.column_name = 'recurring_occurrence_id'"
            )
        )
    assert rule == "RESTRICT"

    # Seeded here rather than relying on another test having run: an ``UPDATE``
    # that matches no rows raises nothing, and would have made this assertion
    # pass for the wrong reason.
    async with database.engine.begin() as connection:
        await _seed(
            connection,
            source="MANUAL",
            code="WRK-2026-090010",
            accepted_at=None,
            completed_at=None,
        )

    async with database.session() as session:
        with pytest.raises(IntegrityError):
            await session.execute(
                text(
                    "UPDATE pr_work_items SET recurring_occurrence_id = gen_random_uuid() "
                    "WHERE code = 'WRK-2026-090010'"
                )
            )
            await session.flush()
        await session.rollback()


# ===========================================================================
# The backfill, and what it deliberately leaves alone
# ===========================================================================


async def _seed_recurring_occurrence(  # type: ignore[no-untyped-def]
    connection, *, key: str, scheduled_for: datetime
) -> uuid.UUID:
    """One template and one occurrence, as ``0036`` would have written them.

    Raw SQL for the same reason ``_seed`` uses it: these rows have to look like
    rows from before ``0037``, not like rows today's mappers would produce.
    """
    user_id = await connection.scalar(text("SELECT id FROM users LIMIT 1"))
    if user_id is None:
        user_id = await connection.scalar(
            text(
                "INSERT INTO users (id, full_name, role, active, created_at, updated_at) "
                "VALUES (gen_random_uuid(), 'Người cũ', 'EMPLOYEE', true, now(), now()) "
                "RETURNING id"
            )
        )
    work_type_id = await connection.scalar(text("SELECT id FROM pr_work_types LIMIT 1"))
    if work_type_id is None:
        work_type_id = await connection.scalar(
            text(
                "INSERT INTO pr_work_types "
                "(id, code, name, category, default_unit, default_quota_basis, "
                " requires_evidence, is_active, display_order, created_at, updated_at) "
                "VALUES (gen_random_uuid(), 'LEGACY', 'Việc cũ', 'OPERATIONS', 'ITEM', "
                " 'ITEM_COUNT', false, true, 0, now(), now()) RETURNING id"
            )
        )
    template_id = await connection.scalar(
        text(
            "INSERT INTO pr_work_recurring_templates "
            "(id, name, work_type_id, assignment_mode, priority, frequency, weekdays, "
            " run_time, start_date, status, revision_no, created_by_user_id, "
            " created_at, updated_at) "
            "VALUES (gen_random_uuid(), 'Routine cũ', :work_type_id, 'SHARED_WORK', "
            " 'NORMAL', 'DAILY', '[]', '09:00', DATE '2026-09-01', 'ACTIVE', 1, "
            " :user_id, now(), now()) RETURNING id"
        ),
        {"work_type_id": work_type_id, "user_id": user_id},
    )
    return await connection.scalar(
        text(
            "INSERT INTO pr_work_recurring_occurrences "
            "(id, template_id, occurrence_key, scheduled_for, template_revision_no, "
            " state, attempts, work_item_count, generated_at, created_at, updated_at) "
            "VALUES (gen_random_uuid(), :template_id, :key, :scheduled_for, 1, "
            " 'GENERATED', 1, 1, now(), now(), now()) RETURNING id"
        ),
        {"template_id": template_id, "key": key, "scheduled_for": scheduled_for},
    )


async def test_05_content_takes_the_milestone_from_the_column_nothing_rewrites(
    dsn: str,
) -> None:
    """``assigned_at``, not ``completed_at``. **The correction.**

    ``create_source_work`` wrote the M3.1 milestone instant into three columns at
    once, so at first glance ``completed_at`` looks like the obvious source. It
    is not safe: ``reopen`` nulls it and a subsequent ``complete`` stamps
    ``now()``, so a content item that was sent back and re-finished carries a
    ``completed_at`` that is the validator's afternoon rather than the day the
    writer delivered.

    ``assigned_at`` is written once and guarded everywhere afterwards - ``accept``
    fills it only when it is null, and it is never null on source work - so it is
    the exact source fact. This test seeds precisely the divergent row and proves
    the migration follows the safe column.
    """
    await downgrade_to(dsn, "0036")
    engine = create_async_engine(dsn, poolclass=None)
    try:
        async with engine.begin() as connection:
            await _seed(
                connection,
                source="CONTENT",
                code="WRK-2026-090001",
                assigned_at=MILESTONE,
                accepted_at=MILESTONE,
                completed_at=MILESTONE,
            )
            # The reopened-and-re-completed row: same milestone, later
            # ``completed_at``. Copying that column would have dated the work to
            # the day somebody pressed a button.
            await _seed(
                connection,
                source="CONTENT",
                code="WRK-2026-090004",
                assigned_at=MILESTONE,
                accepted_at=MILESTONE,
                completed_at=REOPENED_AND_FINISHED,
            )
    finally:
        await engine.dispose()

    await upgrade_to(dsn)

    engine = create_async_engine(dsn, poolclass=None)
    try:
        async with engine.connect() as connection:
            rows = dict(
                (
                    await connection.execute(
                        text(
                            "SELECT code, execution_at FROM pr_work_items "
                            "WHERE code IN ('WRK-2026-090001','WRK-2026-090004')"
                        )
                    )
                ).all()
            )
    finally:
        await engine.dispose()

    assert rows["WRK-2026-090001"] == MILESTONE
    assert rows["WRK-2026-090004"] == MILESTONE
    assert rows["WRK-2026-090004"] != REOPENED_AND_FINISHED


async def test_05b_recurring_is_joined_to_its_occurrence(dsn: str) -> None:
    """**The other correction.** The FK and the date both come from the occurrence.

    ``scheduled_for`` is the canonical statement of when a routine job was owed,
    and it is what the migration recovers - for both key shapes, and for the
    catch-up case where the row was written days after the firing it belongs to.
    ``accepted_at`` is deliberately never consulted.
    """
    await downgrade_to(dsn, "0036")
    engine = create_async_engine(dsn, poolclass=None)
    try:
        async with engine.begin() as connection:
            shared_occurrence = await _seed_recurring_occurrence(
                connection, key="20260902T0900", scheduled_for=FIRING
            )
            separate_occurrence = await _seed_recurring_occurrence(
                connection, key="20260903T0900", scheduled_for=LATER_FIRING
            )
            assignee = await connection.scalar(text("SELECT id FROM users LIMIT 1"))

            # Shared work: one item for everybody.
            await _seed(
                connection,
                source="RECURRING",
                code="WRK-2026-090005",
                source_key=f"recurring:{shared_occurrence}:SHARED",
                # **Days later than the firing** - the catch-up case, and the
                # one ``accepted_at`` would have got wrong.
                accepted_at=GENERATED_LATE,
                assigned_at=GENERATED_LATE,
            )
            # Separate-per-assignee: one item each, keyed on the person.
            await _seed(
                connection,
                source="RECURRING",
                code="WRK-2026-090006",
                source_key=(
                    f"recurring:{separate_occurrence}:A_{uuid.UUID(str(assignee)).hex.upper()}"
                ),
                accepted_at=GENERATED_LATE,
                assigned_at=GENERATED_LATE,
            )
    finally:
        await engine.dispose()

    await upgrade_to(dsn)

    engine = create_async_engine(dsn, poolclass=None)
    try:
        async with engine.connect() as connection:
            rows = {
                code: (occurrence_id, execution_at)
                for code, occurrence_id, execution_at in (
                    await connection.execute(
                        text(
                            "SELECT code, recurring_occurrence_id, execution_at "
                            "FROM pr_work_items "
                            "WHERE code IN ('WRK-2026-090005','WRK-2026-090006')"
                        )
                    )
                ).all()
            }
    finally:
        await engine.dispose()

    assert rows["WRK-2026-090005"][0] == shared_occurrence
    assert rows["WRK-2026-090005"][1] == FIRING
    assert rows["WRK-2026-090006"][0] == separate_occurrence
    assert rows["WRK-2026-090006"][1] == LATER_FIRING
    # Never the generation instant.
    assert rows["WRK-2026-090005"][1] != GENERATED_LATE


async def test_05c_an_unmatched_recurring_row_is_left_null(dsn: str) -> None:
    """**No fabricated provenance.**

    Three ways a key can fail to resolve - malformed, not a uuid at all, and a
    uuid naming an occurrence that does not exist - and all three leave both
    columns null. The alternative was falling back to ``accepted_at``, which
    would have written a plausible-looking wrong date onto exactly the rows
    nobody could afterwards distinguish from correct ones.

    The regex guard is also what stops the third case taking the migration down:
    a cast on a non-uuid segment would raise, and a migration that dies on one
    bad row is a migration nobody can run.
    """
    await downgrade_to(dsn, "0036")
    engine = create_async_engine(dsn, poolclass=None)
    try:
        async with engine.begin() as connection:
            await _seed(
                connection,
                source="RECURRING",
                code="WRK-2026-090007",
                source_key="recurring:not-a-uuid:SHARED",
                accepted_at=GENERATED_LATE,
                assigned_at=GENERATED_LATE,
            )
            await _seed(
                connection,
                source="RECURRING",
                code="WRK-2026-090008",
                source_key="recurring::SHARED",
                accepted_at=GENERATED_LATE,
                assigned_at=GENERATED_LATE,
            )
            await _seed(
                connection,
                source="RECURRING",
                code="WRK-2026-090009",
                # Well-formed, and names no occurrence.
                source_key=f"recurring:{uuid.uuid4()}:SHARED",
                accepted_at=GENERATED_LATE,
                assigned_at=GENERATED_LATE,
            )
    finally:
        await engine.dispose()

    await upgrade_to(dsn)

    engine = create_async_engine(dsn, poolclass=None)
    try:
        async with engine.connect() as connection:
            rows = {
                code: (occurrence_id, execution_at)
                for code, occurrence_id, execution_at in (
                    await connection.execute(
                        text(
                            "SELECT code, recurring_occurrence_id, execution_at "
                            "FROM pr_work_items WHERE code IN "
                            "('WRK-2026-090007','WRK-2026-090008','WRK-2026-090009')"
                        )
                    )
                ).all()
            }
    finally:
        await engine.dispose()

    for code in ("WRK-2026-090007", "WRK-2026-090008", "WRK-2026-090009"):
        assert rows[code] == (None, None), code


async def test_05d_manual_work_is_left_alone(dsn: str) -> None:
    """**Null, and deliberately.** There is nothing to recover.

    A person filing work states a deadline and never an execution date, so no
    column holds one - and the "do not invent a date" rule is expressed here as
    data rather than as a docstring.
    """
    await downgrade_to(dsn, "0036")
    engine = create_async_engine(dsn, poolclass=None)
    try:
        async with engine.begin() as connection:
            await _seed(
                connection,
                source="MANUAL",
                code="WRK-2026-090003",
                accepted_at=datetime(2026, 9, 1, 3, 0, tzinfo=UTC),
                assigned_at=datetime(2026, 9, 1, 3, 0, tzinfo=UTC),
            )
    finally:
        await engine.dispose()

    await upgrade_to(dsn)

    engine = create_async_engine(dsn, poolclass=None)
    try:
        async with engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        "SELECT execution_at, recurring_occurrence_id FROM pr_work_items "
                        "WHERE code = 'WRK-2026-090003'"
                    )
                )
            ).one()
    finally:
        await engine.dispose()

    assert row == (None, None)


async def test_06_the_migration_roundtrips(dsn: str) -> None:
    """``0037 -> 0036 -> 0037``. A downgrade nobody has run does not work.

    The downgrade drops a foreign key as well as two columns, which is the part
    that only fails on a database that enforces them - and the work items
    themselves survive both directions, because no schema rollback should be
    able to delete validated work.
    """
    engine = create_async_engine(dsn, poolclass=None)
    try:
        async with engine.connect() as connection:
            before = await connection.scalar(text("SELECT count(*) FROM pr_work_items"))
    finally:
        await engine.dispose()

    await downgrade_to(dsn, "0036")
    engine = create_async_engine(dsn, poolclass=None)
    try:
        async with engine.connect() as connection:
            assert (
                await connection.scalar(text("SELECT version_num FROM alembic_version")) == "0036"
            )
            missing = (
                (
                    await connection.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_name = 'pr_work_items' AND column_name = ANY(:names)"
                        ),
                        {"names": list(NEW_COLUMNS)},
                    )
                )
                .scalars()
                .all()
            )
            assert missing == []
            # M4B's tables and M1's rows are untouched by either direction.
            assert await connection.scalar(
                text("SELECT to_regclass('pr_work_recurring_occurrences') IS NOT NULL")
            )
            assert await connection.scalar(text("SELECT count(*) FROM pr_work_items")) == before
    finally:
        await engine.dispose()

    await upgrade_to(dsn)
    engine = create_async_engine(dsn, poolclass=None)
    try:
        async with engine.connect() as connection:
            assert (
                await connection.scalar(text("SELECT version_num FROM alembic_version"))
                == alembic_head()
            )
            found = (
                (
                    await connection.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_name = 'pr_work_items' AND column_name = ANY(:names)"
                        ),
                        {"names": list(NEW_COLUMNS)},
                    )
                )
                .scalars()
                .all()
            )
            assert sorted(found) == sorted(NEW_COLUMNS)
            assert await connection.scalar(text("SELECT count(*) FROM pr_work_items")) == before
    finally:
        await engine.dispose()
