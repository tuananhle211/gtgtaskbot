"""The AI review table, on a database built the way production builds one.

Every promise Step 1A1 makes is a *PostgreSQL* promise: four ``CHECK``
constraints, two ``ON DELETE RESTRICT`` foreign keys, and the deliberate
*absence* of a unique index over ``(content_id, reviewed_version)``. None of
them can be proved by the offline fixture that builds a schema from the models
it is checking. So this file does not use ``Base.metadata.create_all`` at all:
it creates an empty database, runs the real Alembic chain through head, and
writes rows the way an application would.

It reuses the migration harness ``tests/integration/test_dispatch_migrations``
established and ``tests/integration/test_pr_core_migrations`` follows - the
same scratch-database-per-run approach, the same ``asyncio.to_thread`` trick
for Alembic's own event loop.

Run it against a PostgreSQL you are willing to have scratch databases created
in and dropped from::

    export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/postgres
    uv run pytest tests/integration/test_pr_ai_review_migrations.py -m integration

Each fixture creates its own uniquely-named ``meobot_air_*`` database and drops
that one afterwards. Nothing else on the server is read or written.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from meobot.core.config import Settings
from meobot.db.base import Base
from meobot.db.models.pr import PrApprovalEvent, PrBrand, PrContentItem, PrTask
from meobot.db.models.pr_ai_review import PrAiReview
from meobot.db.models.user import User
from meobot.db.session import Database
from meobot.domain.identity.models import Role
from meobot.domain.pr.models import (
    PrAiReviewResult,
    PrAiReviewType,
    PrApprovalDecision,
    PrApprovalStage,
    PrWorkflowStage,
)
from tests.integration.test_dispatch_migrations import (
    TEST_DATABASE_URL,
    downgrade_to,
    upgrade_to,
)

# The migrated database is built once for the whole module - fourteen revisions
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

AI_REVIEW_TABLE = "pr_ai_reviews"

#: A review that ran at a known moment, so ordering assertions have something
#: to order by. Every helper below offsets from it.
REVIEWED_AT = datetime(2026, 5, 4, 9, 30, tzinfo=UTC)


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
async def ai_database() -> AsyncIterator[Database]:
    """One migrated database shared by the constraint tests."""
    async for database in _scratch_database("meobot_air"):
        yield database


@pytest_asyncio.fixture(loop_scope="module")
async def fresh_ai_database() -> AsyncIterator[Database]:
    """A migrated database of its own, for tests that move the schema."""
    async for database in _scratch_database("meobot_airmig"):
        yield database


# --- Fixtures that build the rows the constraint tests hang off ------------


async def _make_user(database: Database, name: str) -> uuid.UUID:
    async with database.transaction() as session:
        user = User(full_name=name, role=Role.EMPLOYEE)
        session.add(user)
        await session.flush()
        return user.id


async def _make_content(database: Database, code: str) -> uuid.UUID:
    """A content item, with the brand and owner it cannot exist without."""
    user_id = await _make_user(database, f"Owner {code}")
    async with database.transaction() as session:
        brand = PrBrand(code=f"BRND-{code}", name=f"Brand {code}")
        session.add(brand)
        await session.flush()
        item = PrContentItem(
            code=code,
            title=f"Content {code}",
            brand_id=brand.id,
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


def _review(content_id: uuid.UUID, **overrides: Any) -> PrAiReview:
    """A valid review, so that each test can invalidate exactly one thing."""
    fields: dict[str, Any] = {
        "content_id": content_id,
        "review_type": PrAiReviewType.FULL_REVIEW,
        "reviewed_version": 1,
        "result": PrAiReviewResult.PASS,
        "model_name": "claude-opus-5",
        "prompt_version": "pr-script-review@1",
        "reviewed_at": REVIEWED_AT,
    }
    fields.update(overrides)
    return PrAiReview(**fields)


# --- 22 & 23: the migration goes up and comes back down --------------------


async def test_the_migration_chain_reaches_head_with_the_ai_review_table(
    ai_database: Database,
) -> None:
    """Requirement 22, on a real PostgreSQL."""
    async with ai_database.session() as session:
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
    # The chain runs to head, not to 0014: later revisions sit on top of this
    # one - Step 1C's 0015 is the first - and pinning the number here would
    # fail every time the module grows, which says nothing about Step 1A1.
    assert stamped >= "0014"
    assert AI_REVIEW_TABLE in present
    # The tables Step 1A1 must not have disturbed.
    assert {"pr_content_items", "pr_tasks", "pr_approval_events"} <= present


async def test_downgrading_0014_removes_only_the_ai_review_table(
    fresh_ai_database: Database,
) -> None:
    """Requirement 23.

    Also checks what the downgrade must *not* do. A rollback that took the
    approval history, the content items or ``users`` with it would be
    catastrophic and silent.
    """
    dsn = str(fresh_ai_database._settings.database_url)

    async def table_names() -> set[str]:
        async with fresh_ai_database.session() as session:
            rows = await session.execute(
                text(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
                )
            )
            return {row[0] for row in rows.all()}

    # Step back to 0014 first, so that what this test measures is what *0014's*
    # downgrade removes rather than everything later revisions piled on top.
    # Without this the scratch database is at head, and Step 1C's
    # ``pr_content_versions`` would be counted against 0014.
    await downgrade_to(dsn, "0014")

    before = await table_names()
    assert AI_REVIEW_TABLE in before

    await downgrade_to(dsn, "0013")
    after = await table_names()
    assert before - after == {AI_REVIEW_TABLE}, "the downgrade removed something else"
    assert {"users", "pr_content_items", "pr_tasks", "pr_approval_events"} <= after

    # And it goes back up cleanly, so 0014 is not a one-way door.
    await upgrade_to(dsn, "head")
    assert AI_REVIEW_TABLE in await table_names()


async def test_a_content_item_already_in_ai_review_survives_the_downgrade(
    fresh_ai_database: Database,
) -> None:
    """The stage column is not rewritten in either direction.

    ``AI_REVIEW`` never became a database constraint, so a row already sitting
    in that stage is not data 0014's downgrade is entitled to touch. This is
    the assertion that a rollback does not silently reclassify work.
    """
    dsn = str(fresh_ai_database._settings.database_url)
    content_id = await _make_content(fresh_ai_database, "CNT-2026-000600")
    async with fresh_ai_database.transaction() as session:
        await session.execute(
            text("UPDATE pr_content_items SET workflow_stage = 'AI_REVIEW' WHERE id = :id"),
            {"id": content_id},
        )

    await downgrade_to(dsn, "0013")
    async with fresh_ai_database.session() as session:
        stage = (
            await session.execute(
                text("SELECT workflow_stage FROM pr_content_items WHERE id = :id"),
                {"id": content_id},
            )
        ).scalar_one()
    assert stage == PrWorkflowStage.AI_REVIEW.value

    await upgrade_to(dsn, "head")


async def test_the_models_and_the_migration_describe_the_same_ai_review_schema(
    ai_database: Database,
) -> None:
    """Requirement 24, using Alembic's own comparator rather than a hand list.

    Restricted to ``pr_`` tables for the same reason
    ``tests/integration/test_pr_core_migrations.py`` restricts its own: this
    repository has pre-existing drift between older models and older
    migrations, and widening the assertion would make it fail for reasons Step
    1A1 did not cause and must not fix.
    """
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    def _compare(connection: Any) -> list[Any]:
        context = MigrationContext.configure(
            connection, opts={"compare_type": True, "compare_server_default": True}
        )
        return list(compare_metadata(context, Base.metadata))

    async with ai_database.engine.connect() as connection:
        differences = await connection.run_sync(_compare)

    pr_differences = [difference for difference in differences if "pr_" in str(difference)]
    assert pr_differences == [], pr_differences


# --- 3, 4, 16, 17: the shape of the table, read from the live catalog ------


async def test_the_ai_review_table_has_the_columns_it_promised(ai_database: Database) -> None:
    """Requirements 3, 4, 16 and 17, from ``information_schema``."""
    async with ai_database.session() as session:
        columns = {
            row[0]: (row[1], row[2])
            for row in (
                await session.execute(
                    text(
                        "SELECT column_name, data_type, is_nullable "
                        "FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND table_name = :table"
                    ),
                    {"table": AI_REVIEW_TABLE},
                )
            ).all()
        }
    assert columns, "pr_ai_reviews was not created"

    # Requirement 4: append-only, so no ``updated_at``.
    assert "updated_at" not in columns
    # Requirement 16: both timestamps, both timezone-aware.
    assert columns["reviewed_at"] == ("timestamp with time zone", "NO")
    assert columns["created_at"] == ("timestamp with time zone", "NO")
    # Requirement 17: no human reviewer anywhere on the row.
    assert not [name for name in columns if name.endswith("user_id")]
    # The JSON payloads are JSONB, matching every other JSON column here.
    for name in ("issues", "suggestions", "policy_flags"):
        assert columns[name] == ("jsonb", "YES"), name
    assert columns["score"] == ("numeric", "YES")
    assert columns["model_version"][1] == "YES"
    assert columns["task_id"][1] == "YES"


async def test_every_specified_index_exists_and_none_of_them_is_unique(
    ai_database: Database,
) -> None:
    """The seven access paths, and the uniqueness that must not be there."""
    async with ai_database.session() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT indexname, indexdef FROM pg_indexes "
                    "WHERE schemaname = 'public' AND tablename = :table"
                ),
                {"table": AI_REVIEW_TABLE},
            )
        ).all()
    definitions = dict(rows)

    assert {
        "ix_pr_ai_reviews_content_reviewed_at",
        "ix_pr_ai_reviews_content_version",
        "ix_pr_ai_reviews_task_id",
        "ix_pr_ai_reviews_result",
        "ix_pr_ai_reviews_review_type",
        "ix_pr_ai_reviews_reviewed_at",
        "ix_pr_ai_reviews_model_name",
    } <= set(definitions)

    # Requirement 15: the primary key is the only unique index on this table.
    unique = {name for name, definition in definitions.items() if "UNIQUE" in definition}
    assert unique == {"pk_pr_ai_reviews"}


# --- 5, 6, 18: the foreign keys ---------------------------------------------


async def test_both_foreign_keys_restrict_and_neither_reaches_users(
    ai_database: Database,
) -> None:
    """Requirements 5, 6 and 18, read from the live catalog."""
    async with ai_database.session() as session:
        rows = (
            await session.execute(
                text(
                    """
                    SELECT kcu.column_name, ccu.table_name AS referenced, rc.delete_rule
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                      ON tc.constraint_name = kcu.constraint_name
                    JOIN information_schema.constraint_column_usage ccu
                      ON tc.constraint_name = ccu.constraint_name
                    JOIN information_schema.referential_constraints rc
                      ON tc.constraint_name = rc.constraint_name
                    WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_name = :table
                    """
                ),
                {"table": AI_REVIEW_TABLE},
            )
        ).all()

    assert {(column, referenced, rule) for column, referenced, rule in rows} == {
        ("content_id", "pr_content_items", "RESTRICT"),
        ("task_id", "pr_tasks", "RESTRICT"),
    }
    assert "users" not in {referenced for _, referenced, _ in rows}


async def test_content_id_is_required(ai_database: Database) -> None:
    """Requirement 5, the refusing half - a review of nothing is not a review."""
    with pytest.raises(IntegrityError):
        async with ai_database.transaction() as session:
            await session.execute(
                text(
                    "INSERT INTO pr_ai_reviews "
                    "(id, review_type, reviewed_version, result, model_name, "
                    " prompt_version, reviewed_at) "
                    "VALUES (:id, 'FULL_REVIEW', 1, 'PASS', 'm', 'p', :at)"
                ),
                {"id": uuid.uuid4(), "at": REVIEWED_AT},
            )


async def test_a_review_of_an_unknown_content_item_is_refused(ai_database: Database) -> None:
    with pytest.raises(IntegrityError):
        async with ai_database.transaction() as session:
            session.add(_review(uuid.uuid4()))
            await session.flush()


async def test_task_id_is_optional_and_may_also_be_supplied(ai_database: Database) -> None:
    """Requirement 6. Some reviews stand behind a task; some behind none."""
    content_id = await _make_content(ai_database, "CNT-2026-000100")
    task_id = await _make_task(ai_database, "TSK-2026-000100")
    async with ai_database.transaction() as session:
        session.add(_review(content_id))
        session.add(_review(content_id, task_id=task_id, reviewed_version=2))
        await session.flush()

    async with ai_database.session() as session:
        stored = [
            row[0]
            for row in (
                await session.execute(
                    text(
                        "SELECT task_id FROM pr_ai_reviews "
                        "WHERE content_id = :id ORDER BY reviewed_version"
                    ),
                    {"id": content_id},
                )
            ).all()
        ]
    assert stored == [None, task_id]


async def test_deleting_a_reviewed_content_item_is_refused(ai_database: Database) -> None:
    """AI review history is not cascade-deleted."""
    content_id = await _make_content(ai_database, "CNT-2026-000110")
    async with ai_database.transaction() as session:
        session.add(_review(content_id))

    with pytest.raises(IntegrityError):
        async with ai_database.transaction() as session:
            await session.execute(
                text("DELETE FROM pr_content_items WHERE id = :id"), {"id": content_id}
            )


async def test_deleting_a_task_a_review_points_at_is_refused(ai_database: Database) -> None:
    content_id = await _make_content(ai_database, "CNT-2026-000111")
    task_id = await _make_task(ai_database, "TSK-2026-000111")
    async with ai_database.transaction() as session:
        session.add(_review(content_id, task_id=task_id))

    with pytest.raises(IntegrityError):
        async with ai_database.transaction() as session:
            await session.execute(text("DELETE FROM pr_tasks WHERE id = :id"), {"id": task_id})


# --- 7: reviewed_version ----------------------------------------------------


@pytest.mark.parametrize("version", [0, -1, -99])
async def test_a_review_below_version_one_is_refused(ai_database: Database, version: int) -> None:
    """Requirement 7. There is no draft zero to have reviewed."""
    content_id = await _make_content(ai_database, f"CNT-2026-0002{abs(version):02d}")
    with pytest.raises(IntegrityError):
        async with ai_database.transaction() as session:
            session.add(_review(content_id, reviewed_version=version))
            await session.flush()


async def test_a_review_of_version_one_is_accepted(ai_database: Database) -> None:
    content_id = await _make_content(ai_database, "CNT-2026-000210")
    async with ai_database.transaction() as session:
        session.add(_review(content_id, reviewed_version=1))
        await session.flush()


# --- 8, 9, 10: score --------------------------------------------------------


async def test_a_review_without_a_score_is_accepted(ai_database: Database) -> None:
    """Requirement 8.

    A checklist that either flagged something or did not has no meaningful
    number, and must be recordable without one being invented.
    """
    content_id = await _make_content(ai_database, "CNT-2026-000300")
    async with ai_database.transaction() as session:
        session.add(_review(content_id, review_type=PrAiReviewType.POLICY_COMPLIANCE))
        await session.flush()

    async with ai_database.session() as session:
        score = (
            await session.execute(
                text("SELECT score FROM pr_ai_reviews WHERE content_id = :id"), {"id": content_id}
            )
        ).scalar_one()
    assert score is None


@pytest.mark.parametrize("score", ["0", "0.00", "1.5", "99.99", "100", "100.00"])
async def test_a_score_within_zero_to_one_hundred_is_accepted(
    ai_database: Database, score: str
) -> None:
    """Requirement 9. Both endpoints are inside the range."""
    content_id = await _make_content(ai_database, f"CNT-2026-0003-{score}")
    async with ai_database.transaction() as session:
        session.add(_review(content_id, score=Decimal(score)))
        await session.flush()


@pytest.mark.parametrize("score", ["-0.01", "-1", "100.01", "999.99"])
async def test_a_score_outside_zero_to_one_hundred_is_refused(
    ai_database: Database, score: str
) -> None:
    """Requirement 10."""
    content_id = await _make_content(ai_database, f"CNT-2026-0004-{score}")
    with pytest.raises(IntegrityError):
        async with ai_database.transaction() as session:
            session.add(_review(content_id, score=Decimal(score)))
            await session.flush()


# --- 11 & 12: provenance is not optional and not blank ---------------------


@pytest.mark.parametrize("blank", ["", " ", "   "])
async def test_a_blank_model_name_is_refused(ai_database: Database, blank: str) -> None:
    """Requirement 11.

    A space is exactly as useless as an empty string, and it is what a form
    submits when somebody tabs past a required field.
    """
    content_id = await _make_content(ai_database, f"CNT-2026-0005-{len(blank)}")
    with pytest.raises(IntegrityError):
        async with ai_database.transaction() as session:
            session.add(_review(content_id, model_name=blank))
            await session.flush()


@pytest.mark.parametrize("blank", ["", " ", "   "])
async def test_a_blank_prompt_version_is_refused(ai_database: Database, blank: str) -> None:
    """Requirement 12."""
    content_id = await _make_content(ai_database, f"CNT-2026-0006-{len(blank)}")
    with pytest.raises(IntegrityError):
        async with ai_database.transaction() as session:
            session.add(_review(content_id, prompt_version=blank))
            await session.flush()


@pytest.mark.parametrize("whitespace", ["\t", "\n"])
async def test_a_tab_or_newline_provenance_is_accepted_and_that_is_a_limitation(
    ai_database: Database, whitespace: str
) -> None:
    """The exact reach of the non-empty rule, asserted rather than assumed.

    PostgreSQL's one-argument ``trim()`` strips **spaces only** - not tabs, not
    newlines. So ``length(trim(model_name)) > 0`` refuses ``''`` and ``'   '``
    and accepts ``E'\\t'``. That is true of every ``*_not_empty`` constraint in
    this module, because they all share ``meobot.db.models.pr._not_empty``;
    Step 1A1 inherits the behaviour rather than inventing it, and widening it
    here alone would make these two columns behave unlike ``pr_brands.code``
    and its eleven neighbours.

    Asserted, not merely documented, so that a future change which does widen
    the helper has to come here and update
    ``docs/pr/STEP_1A1_AI_REVIEW_WORKFLOW.md`` at the same time.
    """
    content_id = await _make_content(ai_database, f"CNT-2026-0007-{whitespace!r}")
    async with ai_database.transaction() as session:
        session.add(_review(content_id, model_name=whitespace, prompt_version=whitespace))
        await session.flush()


async def test_a_review_without_a_model_version_is_accepted(ai_database: Database) -> None:
    """Not every provider exposes one, and a review is not worthless without."""
    content_id = await _make_content(ai_database, "CNT-2026-000610")
    async with ai_database.transaction() as session:
        session.add(_review(content_id, model_version=None))
        await session.flush()


# --- 13, 14, 15: history accumulates ---------------------------------------


async def test_the_same_content_and_version_may_be_reviewed_many_times(
    ai_database: Database,
) -> None:
    """Requirement 13.

    A retry after a timeout, or a second opinion, is another row. There is no
    unique index to turn the second attempt into a failure.
    """
    content_id = await _make_content(ai_database, "CNT-2026-000700")
    async with ai_database.transaction() as session:
        for offset, result in enumerate(
            (
                PrAiReviewResult.REVISION_REQUIRED,
                PrAiReviewResult.PASS_WITH_WARNINGS,
                PrAiReviewResult.PASS,
            )
        ):
            session.add(
                _review(
                    content_id,
                    reviewed_version=3,
                    result=result,
                    reviewed_at=REVIEWED_AT + timedelta(hours=offset),
                )
            )
        await session.flush()

    async with ai_database.session() as session:
        results = [
            row[0]
            for row in (
                await session.execute(
                    text(
                        "SELECT result FROM pr_ai_reviews "
                        "WHERE content_id = :id AND reviewed_version = 3 ORDER BY reviewed_at"
                    ),
                    {"id": content_id},
                )
            ).all()
        ]
    assert results == ["REVISION_REQUIRED", "PASS_WITH_WARNINGS", "PASS"]


async def test_one_version_may_carry_a_review_of_each_type(ai_database: Database) -> None:
    """Requirement 14."""
    content_id = await _make_content(ai_database, "CNT-2026-000710")
    async with ai_database.transaction() as session:
        for offset, review_type in enumerate(PrAiReviewType):
            session.add(
                _review(
                    content_id,
                    review_type=review_type,
                    reviewed_at=REVIEWED_AT + timedelta(minutes=offset),
                )
            )
        await session.flush()

    async with ai_database.session() as session:
        types = {
            row[0]
            for row in (
                await session.execute(
                    text("SELECT review_type FROM pr_ai_reviews WHERE content_id = :id"),
                    {"id": content_id},
                )
            ).all()
        }
    assert types == {"SCRIPT_QUALITY", "POLICY_COMPLIANCE", "BRAND_TONE", "FULL_REVIEW"}


async def test_every_review_of_every_version_survives_side_by_side(
    ai_database: Database,
) -> None:
    """Requirement 15.

    Two versions, three reviews each, and nothing collapses them. Historical
    rows are never overwritten because nothing in this module updates one.
    """
    content_id = await _make_content(ai_database, "CNT-2026-000720")
    async with ai_database.transaction() as session:
        for version in (1, 2):
            for attempt in range(3):
                session.add(
                    _review(
                        content_id,
                        reviewed_version=version,
                        reviewed_at=REVIEWED_AT + timedelta(days=version, minutes=attempt),
                    )
                )
        await session.flush()

    async with ai_database.session() as session:
        counted = dict(
            (
                await session.execute(
                    text(
                        "SELECT reviewed_version, count(*) FROM pr_ai_reviews "
                        "WHERE content_id = :id GROUP BY reviewed_version"
                    ),
                    {"id": content_id},
                )
            ).all()
        )
    assert counted == {1: 3, 2: 3}


async def test_the_json_payloads_round_trip(ai_database: Database) -> None:
    """The documented shapes are stored as given - and validated by nothing.

    Step 1A1 stores JSONB and does not police it. This asserts the round trip
    and, deliberately, nothing about the contents: the shape is documented in
    ``docs/pr/STEP_1A1_AI_REVIEW_WORKFLOW.md`` and will be enforced by whatever
    first parses it.
    """
    content_id = await _make_content(ai_database, "CNT-2026-000730")
    issues = [
        {"code": "HOOK_WEAK", "severity": "WARNING", "message": "Opening line is flat."},
        {
            "code": "CLAIM_RISKY",
            "severity": "ERROR",
            "message": "States a medical outcome.",
            "location": "line 12",
        },
    ]
    suggestions = [{"message": "Lead with the customer's question.", "location": "line 1"}]
    policy_flags = [
        {"code": "IP_THIRD_PARTY", "severity": "BLOCKING", "message": "Uses a licensed track."}
    ]
    async with ai_database.transaction() as session:
        session.add(
            _review(
                content_id,
                result=PrAiReviewResult.REVISION_REQUIRED,
                score=Decimal("62.50"),
                summary="Two fixable problems and one licensing question.",
                issues=issues,
                suggestions=suggestions,
                policy_flags=policy_flags,
                model_version="20260501",
            )
        )
        await session.flush()

    async with ai_database.session() as session:
        row = (
            await session.execute(
                text(
                    "SELECT issues, suggestions, policy_flags, score, summary, model_version "
                    "FROM pr_ai_reviews WHERE content_id = :id"
                ),
                {"id": content_id},
            )
        ).one()
    assert row[0] == issues
    assert row[1] == suggestions
    assert row[2] == policy_flags
    assert row[3] == Decimal("62.50")
    assert row[4].startswith("Two fixable")
    assert row[5] == "20260501"


async def test_created_at_is_filled_by_the_database(ai_database: Database) -> None:
    """Insert without mentioning it - the 0011 shape of test."""
    content_id = await _make_content(ai_database, "CNT-2026-000740")
    review_id = uuid.uuid4()
    async with ai_database.transaction() as session:
        await session.execute(
            text(
                "INSERT INTO pr_ai_reviews "
                "(id, content_id, review_type, reviewed_version, result, model_name, "
                " prompt_version, reviewed_at) "
                "VALUES (:id, :content, 'BRAND_TONE', 1, 'PASS', 'm', 'p', :at)"
            ),
            {"id": review_id, "content": content_id, "at": REVIEWED_AT},
        )
    async with ai_database.session() as session:
        created_at = (
            await session.execute(
                text("SELECT created_at FROM pr_ai_reviews WHERE id = :id"), {"id": review_id}
            )
        ).scalar_one()
    assert created_at is not None


# --- 19: the human approval record is untouched and separate ---------------


async def test_the_approval_event_table_is_unchanged_by_this_step(
    ai_database: Database,
) -> None:
    """Requirement 19, read from the live catalog after the chain has run.

    The column set is asserted as "0014's ten, plus what later revisions added",
    with each addition listed - the same shape the unit twin in
    ``tests/unit/test_pr_ai_review_schema_parity.py`` uses. What requirement 19
    is about survives underneath it and is asserted below: no ``updated_at``, a
    reviewer foreign key, and nothing in 0014 that touches this table.
    """
    async with ai_database.session() as session:
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
    assert columns == {
        "id",
        "content_id",
        "task_id",
        "approval_stage",
        "reviewer_user_id",
        "decision",
        "version_reviewed",
        "comment",
        "decided_at",
        "created_at",
        # Step 1F.2.3: which production submission an INTERNAL_REVIEW decision
        # judged. Nullable, written once at insert, and never updated - so the
        # table is still append-only, which is the property this test guards.
        "production_submission_id",
    }
    assert "updated_at" not in columns


async def test_an_ai_review_writes_no_approval_event(ai_database: Database) -> None:
    """The two histories are separate, and stay separate.

    Storing a review leaves ``pr_approval_events`` empty for that item. When a
    person later decides, their event carries their user id and the AI review
    carries none - which is the whole distinction this step exists to keep.
    """
    content_id = await _make_content(ai_database, "CNT-2026-000800")
    async with ai_database.transaction() as session:
        session.add(_review(content_id, result=PrAiReviewResult.PASS_WITH_WARNINGS))

    async with ai_database.session() as session:
        approvals = (
            await session.execute(
                text("SELECT count(*) FROM pr_approval_events WHERE content_id = :id"),
                {"id": content_id},
            )
        ).scalar_one()
    assert approvals == 0

    reviewer_id = await _make_user(ai_database, "Team lead")
    async with ai_database.transaction() as session:
        session.add(
            PrApprovalEvent(
                content_id=content_id,
                approval_stage=PrApprovalStage.TEAM_LEAD_REVIEW,
                reviewer_user_id=reviewer_id,
                # Rule 9: a human may still reject what the AI passed.
                decision=PrApprovalDecision.REJECTED,
                version_reviewed=1,
                decided_at=REVIEWED_AT + timedelta(hours=2),
            )
        )

    async with ai_database.session() as session:
        decisions = (
            await session.execute(
                text(
                    "SELECT decision, reviewer_user_id FROM pr_approval_events "
                    "WHERE content_id = :id"
                ),
                {"id": content_id},
            )
        ).all()
        ai_results = (
            await session.execute(
                text("SELECT result FROM pr_ai_reviews WHERE content_id = :id"),
                {"id": content_id},
            )
        ).all()
    assert decisions == [("REJECTED", reviewer_id)]
    assert ai_results == [("PASS_WITH_WARNINGS",)]


# --- The workflow stage the database never policed -------------------------


async def test_the_stage_column_accepts_ai_review_without_any_ddl(
    ai_database: Database,
) -> None:
    """The exact consequence of Part 1, on a real database.

    ``AI_REVIEW`` needed no migration because the column is a ``VARCHAR(30)``
    with no ``ENUM`` type and no vocabulary ``CHECK``. This proves the value
    stores - and, in the same breath, that the database is *not* what is
    keeping the vocabulary honest.
    """
    content_id = await _make_content(ai_database, "CNT-2026-000900")
    async with ai_database.transaction() as session:
        session.add(_review(content_id))
        await session.execute(
            text("UPDATE pr_content_items SET workflow_stage = :stage WHERE id = :id"),
            {"stage": PrWorkflowStage.AI_REVIEW.value, "id": content_id},
        )

    async with ai_database.session() as session:
        stage = (
            await session.execute(
                text("SELECT workflow_stage FROM pr_content_items WHERE id = :id"),
                {"id": content_id},
            )
        ).scalar_one()
        constraints = {
            row[0]
            for row in (
                await session.execute(
                    text(
                        "SELECT conname FROM pg_constraint "
                        "WHERE conrelid = 'pr_content_items'::regclass AND contype = 'c'"
                    )
                )
            ).all()
        }
        stage_type = (
            await session.execute(
                text(
                    "SELECT data_type, character_maximum_length FROM information_schema.columns "
                    "WHERE table_name = 'pr_content_items' AND column_name = 'workflow_stage'"
                )
            )
        ).one()
    assert stage == "AI_REVIEW"
    assert stage_type == ("character varying", 30)
    # No vocabulary CHECK was created by 0012 and none was added by 0014.
    assert not [name for name in constraints if "workflow" in name or "stage" in name]
