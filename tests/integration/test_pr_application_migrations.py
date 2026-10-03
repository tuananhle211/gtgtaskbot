"""Step 1C on a database built the way production builds one.

Two things live here because they cannot be true anywhere else:

* **Migration 0015 applies and reverses** against a real PostgreSQL, and the
  table it creates matches the model Alembic autogenerate would compare it to.
* **``SELECT ... FOR UPDATE`` actually serialises two concurrent commands.**
  The offline suite runs on SQLite, where
  :func:`~meobot.application.pr_support.lock_row` deliberately degrades to a
  plain ``get`` - so "the lock works" is a claim only PostgreSQL can settle.
  Without it, two callers approving the same content both read
  ``TEAM_LEAD_REVIEW``, both find their decision legal, and the second silently
  overwrites the first's outcome.

Run it against a PostgreSQL you are willing to have scratch databases created
in and dropped from::

    export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/postgres
    uv run pytest tests/integration/test_pr_application_migrations.py -m integration

Each fixture creates its own uniquely-named ``meobot_pr1c_*`` database and drops
that one afterwards. Nothing else on the server is read or written.
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
from meobot.application.pr_content_service import (
    CreateContentCommand,
    PrContentService,
)
from meobot.application.pr_workflow_service import PrContentWorkflowService
from meobot.core.config import Settings, get_settings
from meobot.db.base import Base
from meobot.db.models.pr import PrBrand
from meobot.db.models.pr_content_version import PrContentVersion
from meobot.db.models.user import User
from meobot.db.session import Database
from meobot.domain.identity.models import Actor, Role
from meobot.domain.pr.errors import PrStaleVersionError, PrWorkflowTransitionError
from meobot.domain.pr.models import PrWorkflowStage
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

VERSIONS_TABLE = "pr_content_versions"
NOW = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)


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
    """One migrated database shared by the schema tests."""
    async for database in _scratch_database("meobot_pr1c"):
        yield database


@pytest_asyncio.fixture(loop_scope="module")
async def fresh_database() -> AsyncIterator[Database]:
    """A migrated database of its own, for tests that move the schema."""
    async for database in _scratch_database("meobot_pr1cmig"):
        yield database


# --- Migration -------------------------------------------------------------


async def test_the_chain_reaches_0015_with_the_versions_table(pr_database: Database) -> None:
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
    # ``>=`` rather than ``==``: later revisions sit on top of this one -
    # Step 1C.1's 0016 is the first - and pinning the number would fail
    # every time the module grows.
    assert stamped >= "0015"
    assert VERSIONS_TABLE in present
    # Every table Step 1C builds on is still there and unchanged.
    assert {
        "pr_content_items",
        "pr_approval_events",
        "pr_ai_reviews",
        "pr_publications",
        "pr_post_metric_snapshots",
    } <= present


async def test_downgrading_0015_removes_only_the_versions_table(
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

    # Step back to 0015 first, so this measures what *0015's* downgrade
    # removes rather than everything later revisions piled on top.
    await downgrade_to(dsn, "0015")

    before = await table_names()
    assert VERSIONS_TABLE in before

    await downgrade_to(dsn, "0014")
    after = await table_names()
    assert before - after == {VERSIONS_TABLE}, "the downgrade removed something else"
    assert {"users", "pr_content_items", "pr_ai_reviews", "pr_approval_events"} <= after

    await upgrade_to(dsn, "head")
    assert VERSIONS_TABLE in await table_names()


async def test_the_model_and_the_migration_describe_the_same_schema(
    pr_database: Database,
) -> None:
    """Parity, using Alembic's own comparator rather than a hand list."""
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


async def test_content_versions_are_append_only_and_restrict(pr_database: Database) -> None:
    """No ``updated_at``, and neither foreign key cascades."""
    async with pr_database.session() as session:
        columns = {
            row[0]
            for row in (
                await session.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND table_name = :table"
                    ),
                    {"table": VERSIONS_TABLE},
                )
            ).all()
        }
        foreign_keys = (
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
                {"table": VERSIONS_TABLE},
            )
        ).all()

    assert "updated_at" not in columns
    assert "created_at" in columns
    assert {"content_id", "version_no", "title", "script_text", "change_note"} <= columns
    assert {(column, referenced, rule) for column, referenced, rule in foreign_keys} == {
        ("content_id", "pr_content_items", "RESTRICT"),
        ("created_by_user_id", "users", "RESTRICT"),
    }


# --- Constraints, against the real database -------------------------------


async def _seed_content(database: Database, label: str) -> tuple[uuid.UUID, uuid.UUID, Actor]:
    """A user, a brand and one content item at ``IDEA`` with version 1.

    ``label`` only names the brand now - the content code is allocated by
    :class:`~meobot.application.pr_code_service.PrCodeService`.
    """
    async with database.transaction() as session:
        user = User(full_name=f"Author {label}", role=Role.OWNER)
        brand = PrBrand(code=f"BRND-{label}", name=f"Brand {label}")
        session.add_all([user, brand])
        await session.flush()
        user_id, brand_id = user.id, brand.id

    actor = Actor(user_id=user_id, full_name="Author", role=Role.OWNER)
    async with database.transaction() as session:
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
                title="First draft",
                brand_id=brand_id,
                owner_user_id=user_id,
                script_text="Draft one.",
            ),
        )
        content_id = snapshot.content.id
    return content_id, user_id, actor


async def test_a_duplicate_version_number_is_refused_by_the_database(
    pr_database: Database,
) -> None:
    """The unique index is what makes the service's ``latest + 1`` safe."""
    content_id, user_id, _ = await _seed_content(pr_database, "CNT-PG-DUP")
    with pytest.raises(IntegrityError):
        async with pr_database.transaction() as session:
            session.add(
                PrContentVersion(
                    content_id=content_id,
                    version_no=1,
                    title="Impostor",
                    created_by_user_id=user_id,
                )
            )
            await session.flush()


@pytest.mark.parametrize("version_no", [0, -1])
async def test_a_version_below_one_is_refused(pr_database: Database, version_no: int) -> None:
    content_id, user_id, _ = await _seed_content(pr_database, f"CNT-PG-V{abs(version_no)}")
    with pytest.raises(IntegrityError):
        async with pr_database.transaction() as session:
            session.add(
                PrContentVersion(
                    content_id=content_id,
                    version_no=version_no,
                    title="Zeroth",
                    created_by_user_id=user_id,
                )
            )
            await session.flush()


async def test_a_blank_version_title_is_refused(pr_database: Database) -> None:
    content_id, user_id, _ = await _seed_content(pr_database, "CNT-PG-BLANK")
    with pytest.raises(IntegrityError):
        async with pr_database.transaction() as session:
            session.add(
                PrContentVersion(
                    content_id=content_id,
                    version_no=2,
                    title="   ",
                    created_by_user_id=user_id,
                )
            )
            await session.flush()


async def test_deleting_content_that_has_versions_is_refused(pr_database: Database) -> None:
    """Draft history does not vanish as a side effect of tidying up."""
    content_id, _, _ = await _seed_content(pr_database, "CNT-PG-RESTRICT")
    with pytest.raises(IntegrityError):
        async with pr_database.transaction() as session:
            await session.execute(
                text("DELETE FROM pr_content_items WHERE id = :id"), {"id": content_id}
            )


# --- Concurrency, which only PostgreSQL can settle -------------------------


async def test_two_concurrent_transitions_do_not_both_succeed(pr_database: Database) -> None:
    """The row lock, exercised for real.

    Both coroutines start from ``SCRIPTING`` and ask for ``AI_REVIEW``. The
    first takes the row lock, moves the content and commits; the second blocks
    on ``FOR UPDATE`` until that commit, then re-reads a stage that is no
    longer ``SCRIPTING`` and is refused by the matrix.

    Exactly one succeeds. On SQLite this test would be meaningless - there is
    no ``FOR UPDATE`` clause and ``lock_row`` degrades to a plain read - which
    is why it lives here.
    """
    content_id, _, actor = await _seed_content(pr_database, "CNT-PG-RACE")
    async with pr_database.transaction() as session:
        audit = AuditService(session)
        workflow = PrContentWorkflowService(session, audit, PrCapabilityService(session, audit))
        for stage in (PrWorkflowStage.BRIEFING, PrWorkflowStage.SCRIPTING):
            await workflow.request_transition(
                actor=actor, request_id=uuid.uuid4(), content_id=content_id, target=stage
            )

    async def advance() -> str:
        try:
            async with pr_database.transaction() as session:
                audit = AuditService(session)
                workflow = PrContentWorkflowService(
                    session, audit, PrCapabilityService(session, audit)
                )
                await workflow.request_transition(
                    actor=actor,
                    request_id=uuid.uuid4(),
                    content_id=content_id,
                    target=PrWorkflowStage.AI_REVIEW,
                )
        except PrWorkflowTransitionError:
            return "refused"
        return "moved"

    outcomes = await asyncio.gather(advance(), advance())
    assert sorted(outcomes) == ["moved", "refused"]

    async with pr_database.session() as session:
        stage = (
            await session.execute(
                text("SELECT workflow_stage FROM pr_content_items WHERE id = :id"),
                {"id": content_id},
            )
        ).scalar_one()
        versions = (
            await session.execute(
                text("SELECT count(*) FROM pr_content_versions WHERE content_id = :id"),
                {"id": content_id},
            )
        ).scalar_one()
    assert stage == PrWorkflowStage.AI_REVIEW.value
    assert versions == 1


async def test_two_concurrent_revisions_do_not_produce_two_version_twos(
    pr_database: Database,
) -> None:
    """The same lock, protecting version allocation rather than a stage.

    Both callers hold ``expected_version=1``. One writes version 2; the other
    either finds the version stale after waiting, or loses on the unique index.
    Either way there is exactly one version 2, which is the property that
    matters - a second one would mean two drafts sharing a number that reviews
    point at.
    """
    content_id, _, actor = await _seed_content(pr_database, "CNT-PG-RACE2")

    async def revise(title: str) -> str:
        from meobot.application.pr_content_service import ReviseContentCommand

        try:
            async with pr_database.transaction() as session:
                audit = AuditService(session)
                service = PrContentService(
                    session,
                    audit,
                    PrCapabilityService(session, audit),
                    PrCodeService(session, get_settings()),
                )
                await service.revise_content(
                    actor=actor,
                    request_id=uuid.uuid4(),
                    command=ReviseContentCommand(
                        content_id=content_id, expected_version=1, title=title
                    ),
                )
        except PrStaleVersionError:
            # The lock did its job: this caller waited, re-read, and found the
            # version it was holding is no longer the latest.
            return "stale"
        except IntegrityError:
            # The lock was not held long enough to matter and the unique index
            # caught it instead. Also correct, and also not a second version 2.
            return "rejected"
        return "written"

    outcomes = await asyncio.gather(revise("A"), revise("B"))
    assert outcomes.count("written") == 1, outcomes

    async with pr_database.session() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT version_no, count(*) FROM pr_content_versions "
                    "WHERE content_id = :id GROUP BY version_no ORDER BY version_no"
                ),
                {"id": content_id},
            )
        ).all()
    assert [(version, count) for version, count in rows] == [(1, 1), (2, 1)]


async def test_a_failed_command_leaves_no_partial_state(pr_database: Database) -> None:
    """One command, one transaction: the version and the stage move together.

    The transition is asked for *after* a version is written in the same
    transaction, and then fails. Neither survives - which is the property
    "no partial state may remain" actually means.
    """
    content_id, _, actor = await _seed_content(pr_database, "CNT-PG-ATOMIC")

    from meobot.application.pr_content_service import ReviseContentCommand

    with pytest.raises(PrWorkflowTransitionError):
        async with pr_database.transaction() as session:
            audit = AuditService(session)
            capabilities = PrCapabilityService(session, audit)
            content_service = PrContentService(
                session, audit, capabilities, PrCodeService(session, get_settings())
            )
            workflow = PrContentWorkflowService(session, audit, capabilities)
            await content_service.revise_content(
                actor=actor,
                request_id=uuid.uuid4(),
                command=ReviseContentCommand(
                    content_id=content_id, expected_version=1, title="Second draft"
                ),
            )
            # Illegal from IDEA: this raises, and the version above must go
            # with it.
            await workflow.request_transition(
                actor=actor,
                request_id=uuid.uuid4(),
                content_id=content_id,
                target=PrWorkflowStage.PUBLISHED,
            )

    async with pr_database.session() as session:
        versions = (
            await session.execute(
                text("SELECT count(*) FROM pr_content_versions WHERE content_id = :id"),
                {"id": content_id},
            )
        ).scalar_one()
        stage = (
            await session.execute(
                text("SELECT workflow_stage FROM pr_content_items WHERE id = :id"),
                {"id": content_id},
            )
        ).scalar_one()
    assert versions == 1
    assert stage == PrWorkflowStage.IDEA.value
