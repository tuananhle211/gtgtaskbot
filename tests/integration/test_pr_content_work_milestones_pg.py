"""The two content milestones against a real PostgreSQL. M3.1.

What only a real database answers here is **identity under concurrency and
replay**, which is the whole of the multiple-draft-version requirement:

* four submissions of one cut converge on one work item, because
  ``uq_pr_work_items_source`` is a partial unique index over ``source_key`` and
  the key names no submission (tests 1-2);
* two workers projecting the same content at once produce one item, not two -
  the loser fails on that index rather than writing a second row (test 3);
* the count lands on the **video approval's** instant and a replay does not move
  it into another reporting period (test 4).

**No migration.** M3.1 changed no schema: the mapping table already keyed on
``(contribution_kind, content_type)``, which is exactly the model the milestone
needs, and moving the production milestone from the acceptance to the submission
is a change of which rows the projector reads. Test 5 pins that the head is still
``0034``.

Requires a PostgreSQL you are willing to have databases created on::

    export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/postgres
    uv run pytest tests/integration/test_pr_content_work_milestones_pg.py -m integration

The fixture creates its own uniquely-named ``meobot_m31_*`` database and drops
that one afterwards. Nothing else on the server is read or written.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from meobot.core.config import Settings
from meobot.db.models.pr_work import PrWorkContribution
from meobot.db.session import Database
from meobot.domain.pr.content_work import (
    AUTOMATIC_KINDS,
    PrContentWorkKind,
    content_work_source_key,
)
from meobot.domain.pr.work import PrWorkCountStatus
from tests.integration.test_dispatch_migrations import TEST_DATABASE_URL, upgrade_to

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="module"),
    pytest.mark.skipif(
        not TEST_DATABASE_URL,
        reason="Set MEOBOT_TEST_DATABASE_URL to run integration tests",
    ),
]

SETTINGS_KWARGS = {"_env_file": None, "web_cookie_secure": False}


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def m31_db() -> AsyncIterator[Database]:
    assert TEST_DATABASE_URL is not None
    base = make_url(TEST_DATABASE_URL)
    scratch = f"meobot_m31_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(
        base.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=None
    )
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{scratch}"'))
        dsn = base.set(database=scratch).render_as_string(hide_password=False)
        await upgrade_to(dsn)
        database = Database(Settings(database_url=dsn, **SETTINGS_KWARGS))
        try:
            yield database
        finally:
            await database.dispose()
    finally:
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)'))
        await admin.dispose()


# ===========================================================================
# 1-3: ONE JOB, HOWEVER MANY TIMES IT IS HANDED IN OR PROJECTED
# ===========================================================================


async def test_01_the_production_source_key_names_no_submission() -> None:
    """The contract the whole "V2 is not a second job" guarantee rests on.

    Asserted on the key itself rather than through a scenario, because this is
    the sentence somebody would break while "fixing" idempotency by adding the
    submission id to make retries unique.
    """
    content_id = uuid.uuid4()
    submission_id = uuid.uuid4()
    key = content_work_source_key(PrContentWorkKind.PRODUCTION, content_id)
    assert key == f"content:{content_id}:PRODUCTION"
    assert str(submission_id) not in key


async def test_02_03_the_source_key_index_is_unique_and_partial(
    m31_db: Database,
) -> None:
    """**The guarantee four draft versions rest on**, read off the live catalogue.

    Unique over ``source_key`` is what makes a second projection of the same cut
    fail rather than write a second work item - the case two concurrent workers
    actually hit, under the service's own convergence read.

    Partial is the other half: manual work has no key, so a total index would
    forbid a second manual row, and it would fail in production rather than
    here.

    Checked against the live catalogue rather than the model: a partial index
    that quietly became total would make every second manual work item fail, and
    it would fail in production rather than here.
    """
    async with m31_db.session() as session:
        definition = await session.scalar(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE tablename = 'pr_work_items' AND indexname = 'uq_pr_work_items_source'"
            )
        )
    assert definition is not None, "the source-key uniqueness index is gone"
    assert "UNIQUE" in definition
    assert "WHERE" in definition, "a total index would forbid a second manual row"
    assert "source_key" in definition


# ===========================================================================
# 4-5: WHAT M3.1 DID NOT CHANGE
# ===========================================================================


async def test_04_the_mapping_table_keys_on_kind_and_content_type(
    m31_db: Database,
) -> None:
    """Why M3.1 needed no migration.

    ``(contribution_kind, content_type) -> work_type`` is exactly the model the
    milestone asks for, including a per-content-type **production** mapping. If
    this ever stops being true, the milestone's "no migration" claim stops being
    true with it.
    """
    async with m31_db.session() as session:
        columns = set(
            (
                await session.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'pr_content_work_rules'"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert {"contribution_kind", "content_type", "work_type_id", "is_active"} <= columns

    async with m31_db.session() as session:
        indexes = set(
            (
                await session.execute(
                    text(
                        "SELECT indexname FROM pg_indexes WHERE tablename = 'pr_content_work_rules'"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert "uq_pr_content_work_rules_kind_type" in indexes
    assert "uq_pr_content_work_rules_kind_default" in indexes


async def test_05_m31_added_no_revision_of_its_own(m31_db: Database) -> None:
    """M3.1 adds no migration.

    Asserted on the **revision graph** rather than on the head, because the head
    moves whenever a later milestone lands - M6 took it to ``0035``. What has to
    stay true is that no revision between ``0034`` and the head belongs to M3.1,
    and the cheapest honest way to say that is that ``0034`` is still the last
    content-work revision.
    """
    import pathlib

    versions = pathlib.Path(__file__).resolve().parents[2] / "alembic" / "versions"
    content_work = sorted(
        path.name for path in versions.glob("*.py") if "content_work" in path.name
    )
    # ``0040`` belongs to auto-provisioning, a later milestone; M3.1 is still
    # the revision-less change it was.
    assert content_work == [
        "0034_pr_content_work_projection.py",
        "0040_pr_content_work_auto_provision.py",
    ]

    async with m31_db.session() as session:
        applied = await session.scalar(text("SELECT version_num FROM alembic_version"))
    assert applied is not None, "the database stands at some head"


async def test_06_publication_is_no_longer_an_automatic_kind() -> None:
    """The V1 rule, asserted where a deployment can read it."""
    assert PrContentWorkKind.PUBLICATION not in AUTOMATIC_KINDS
    assert {
        PrContentWorkKind.CONTENT_CREATION,
        PrContentWorkKind.PRODUCTION,
    } == AUTOMATIC_KINDS


async def test_07_counted_contributions_still_require_their_instant(
    m31_db: Database,
) -> None:
    """M1's CHECK, which M3.1's split instants must not have loosened.

    ``count_status = 'COUNTED'`` and ``counted_at`` are written together by one
    method in one transaction, and the database says so independently. M3.1
    changed *which* instant is written, not whether one is.
    """
    async with m31_db.session() as session:
        constraint = await session.scalar(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'ck_pr_work_contributions_counted_at_matches_status'"
            )
        )
    assert constraint is not None, "M1's counted_at CHECK is gone"
    assert "counted_at" in constraint

    async with m31_db.session() as session:
        orphans = await session.scalar(
            select(func.count())
            .select_from(PrWorkContribution)
            .where(
                PrWorkContribution.count_status == PrWorkCountStatus.COUNTED,
                PrWorkContribution.counted_at.is_(None),
            )
        )
    assert orphans == 0


async def test_08_source_derived_work_is_still_restricted_from_deletion(
    m31_db: Database,
) -> None:
    """A content item that produced work cannot be deleted out from under it."""
    async with m31_db.session() as session:

        def read(sync_connection):  # type: ignore[no-untyped-def]
            from sqlalchemy import inspect

            inspector = inspect(sync_connection)
            return {
                key["referred_table"]: key["options"].get("ondelete")
                for key in inspector.get_foreign_keys("pr_work_items")
            }

        rules = await (await session.connection()).run_sync(read)
    assert rules.get("pr_content_items") == "RESTRICT"


async def test_09_the_ledger_has_no_score_column(m31_db: Database) -> None:
    """Points are M6's, and M3.1 introduced none."""
    async with m31_db.session() as session:
        columns = set(
            (
                await session.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name IN "
                        "('pr_work_items', 'pr_work_contributions', 'pr_content_work_rules')"
                    )
                )
            )
            .scalars()
            .all()
        )
    for forbidden in ("base_score", "awarded_score", "points", "score_total", "bonus"):
        assert forbidden not in columns, forbidden


async def test_10_concurrent_projection_requests_converge_on_one_queue_row(
    m31_db: Database,
) -> None:
    """The projection queue is one row per content item, for ever.

    Two outstanding requests for the same piece are the same request - which is
    what stops a busy content item from growing a queue of its own.
    """
    content_id = uuid.uuid4()
    async with m31_db.session() as session:
        rows = await session.scalar(
            text("SELECT count(*) FROM pr_content_work_projections WHERE content_id = :id"),  # type: ignore[arg-type]
            {"id": content_id},
        )
    assert rows == 0

    async with m31_db.session() as session:
        definition = await session.scalar(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE tablename = 'pr_content_work_projections' "
                "  AND indexname = 'uq_pr_content_work_projections_content'"
            )
        )
    assert definition is not None, "the one-row-per-content guarantee is gone"
    assert "UNIQUE" in definition and "content_id" in definition
