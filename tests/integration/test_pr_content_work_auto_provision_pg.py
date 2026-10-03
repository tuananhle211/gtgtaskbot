"""Auto-provisioning against a real PostgreSQL: ``0040``, and the race.

What only a real database answers here:

* **the models describe the migration.** ``compare_metadata`` finds nothing to
  say about ``pr_content_work_rules`` at head (test 1);
* **the migration goes both ways, and refuses to guess.** ``0040 -> 0039`` with
  a rule a person wrote; ``0040 -> 0039`` with a rule the system wrote is
  refused rather than attributed to somebody (tests 2-3);
* **two workers meeting one unseen content type produce one type, one
  binding, and each their own result.** The unique indexes on
  ``pr_work_types.code`` and ``uq_pr_content_work_rules_kind_type`` are the
  guarantee; the savepoint-and-re-read in the two services is what turns the
  loser's conflict into reuse rather than a failed projection (tests 4-5).

Requires a PostgreSQL you are willing to have databases created on::

    export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/postgres
    uv run pytest tests/integration/test_pr_content_work_auto_provision_pg.py -m integration

The fixture creates its own uniquely-named ``meobot_0040_*`` database and drops
that one afterwards. Nothing else on the server is read or written.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

import meobot.db.models  # noqa: F401 - registers every model for compare_metadata
from meobot.application.pr_services import build_pr_services
from meobot.core.config import Settings
from meobot.db.base import Base
from meobot.db.models.pr_content_work import PrContentWorkRule
from meobot.db.models.pr_work import PrWorkItem, PrWorkType
from meobot.db.models.pr_work_result import PrWorkResult
from meobot.db.session import Database
from meobot.domain.identity.models import Actor, Role
from meobot.domain.pr.content_work import (
    AUTO_WORK_TYPE_CATEGORIES,
    AUTO_WORK_TYPE_UNIT,
    PrContentWorkKind,
    auto_work_type_code,
    auto_work_type_name,
    content_work_source_key,
)
from meobot.domain.pr.models import PrContentType
from meobot.domain.pr.work import PrWorkCountStatus
from meobot.domain.pr.work_results import PrWorkResultSource
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

KIND = PrContentWorkKind.CONTENT_CREATION
TYPE = PrContentType.LONG_YOUTUBE_SCRIPT
CODE = auto_work_type_code(KIND, TYPE)
SEPTEMBER = datetime(2026, 9, 15, 3, 0, tzinfo=UTC)

WRITER = uuid.uuid4()
HEAD = uuid.uuid4()
PERIOD = uuid.uuid4()
MANUAL_TYPE = uuid.uuid4()


def system_actor() -> Actor:
    return Actor(user_id=None, full_name="meobot-worker", role=Role.OWNER, is_bootstrap_owner=True)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def dsn() -> AsyncIterator[str]:
    """A scratch database at head, with two people, a month and a manual type."""
    assert TEST_DATABASE_URL is not None
    base = make_url(TEST_DATABASE_URL)
    scratch = f"meobot_0040_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(
        base.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=None
    )
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{scratch}"'))
        url = base.set(database=scratch).render_as_string(hide_password=False)
        await upgrade_to(url)
        engine = create_async_engine(url)
        try:
            async with engine.begin() as connection:
                await _seed(connection)
        finally:
            await engine.dispose()
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


async def _seed(connection) -> None:  # type: ignore[no-untyped-def]
    for user_id, name, role in (
        (WRITER, "Người viết", "EMPLOYEE"),
        (HEAD, "Trưởng phòng", "ADMIN"),
    ):
        await connection.execute(
            text(
                "INSERT INTO users (id, full_name, role, active, created_at, updated_at) "
                "VALUES (:id, :name, :role, true, now(), now())"
            ),
            {"id": user_id, "name": name, "role": role},
        )
    await connection.execute(
        text(
            "INSERT INTO pr_reporting_periods "
            "(id, code, period_type, date_start, date_end, status, created_at, updated_at) "
            "VALUES (:id, '2026-09', 'MONTH', '2026-09-01', '2026-09-30', 'OPEN', now(), now())"
        ),
        {"id": PERIOD},
    )
    await connection.execute(
        text(
            "INSERT INTO pr_work_types "
            "(id, code, name, category, default_unit, default_quota_basis, requires_evidence, "
            " is_active, display_order, created_at, updated_at) "
            "VALUES (:id, 'MANUAL_SCRIPT', 'Kịch bản', 'CONTENT', 'ITEM', 'ITEM_COUNT', false, "
            " true, 0, now(), now())"
        ),
        {"id": MANUAL_TYPE},
    )


# ===========================================================================
# 1-3: THE MIGRATION
# ===========================================================================


async def test_01_models_and_migration_agree_at_head(database: Database) -> None:
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    def _compare(sync_connection):  # type: ignore[no-untyped-def]
        context = MigrationContext.configure(
            sync_connection, opts={"compare_type": True, "compare_server_default": True}
        )
        return list(compare_metadata(context, Base.metadata))

    async with database.engine.connect() as connection:
        differences = await connection.run_sync(_compare)
    ours = [one for one in differences if "pr_content_work_rules" in str(one)]
    assert ours == [], ours

    async with database.session() as session:
        head = await session.scalar(text("SELECT version_num FROM alembic_version"))
        nullable = await session.scalar(
            text(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_name = 'pr_content_work_rules' "
                "  AND column_name = 'created_by_user_id'"
            )
        )
    assert head == alembic_head() == "0041"
    assert nullable == "YES"


async def test_02_downgrade_and_upgrade_again_with_a_human_rule(
    dsn: str, database: Database
) -> None:
    """``0040 -> 0039 -> 0040``. A rule a person wrote survives both ways."""
    rule_id = uuid.uuid4()
    async with database.session() as session:
        await session.execute(
            text(
                "INSERT INTO pr_content_work_rules "
                "(id, contribution_kind, content_type, work_type_id, is_active, note, "
                " created_by_user_id, created_at, updated_at) "
                "VALUES (:id, 'PRODUCTION', 'CORPORATE_TVC', :type_id, true, NULL, :user, "
                " now(), now())"
            ),
            {"id": rule_id, "type_id": MANUAL_TYPE, "user": HEAD},
        )
        await session.commit()
    await database.dispose()

    await downgrade_to(dsn, "0039")
    engine = create_async_engine(dsn)
    try:
        async with engine.connect() as connection:
            nullable = await connection.scalar(
                text(
                    "SELECT is_nullable FROM information_schema.columns "
                    "WHERE table_name = 'pr_content_work_rules' "
                    "  AND column_name = 'created_by_user_id'"
                )
            )
            assert nullable == "NO"
            version = await connection.scalar(text("SELECT version_num FROM alembic_version"))
            assert version == "0039"
            kept = await connection.scalar(
                text("SELECT created_by_user_id FROM pr_content_work_rules WHERE id = :id"),
                {"id": rule_id},
            )
            assert kept == HEAD
    finally:
        await engine.dispose()

    await upgrade_to(dsn)
    engine = create_async_engine(dsn)
    try:
        async with engine.connect() as connection:
            version = await connection.scalar(text("SELECT version_num FROM alembic_version"))
            assert version == "0041"
    finally:
        await engine.dispose()


async def test_03_downgrade_refuses_to_attribute_a_system_rule(
    dsn: str, database: Database
) -> None:
    """There is no person to name. The migration stops and says so."""
    rule_id = uuid.uuid4()
    async with database.session() as session:
        await session.execute(
            text(
                "INSERT INTO pr_content_work_rules "
                "(id, contribution_kind, content_type, work_type_id, is_active, note, "
                " created_by_user_id, created_at, updated_at) "
                "VALUES (:id, 'PRODUCTION', 'PRESS_ARTICLE', :type_id, true, NULL, NULL, "
                " now(), now())"
            ),
            {"id": rule_id, "type_id": MANUAL_TYPE},
        )
        await session.commit()
    await database.dispose()

    with pytest.raises(Exception, match="auto-provisioned"):
        await downgrade_to(dsn, "0039")

    engine = create_async_engine(dsn)
    try:
        async with engine.connect() as connection:
            version = await connection.scalar(text("SELECT version_num FROM alembic_version"))
            assert version == "0041", "a refused downgrade moves nothing"
            await connection.execute(
                text("DELETE FROM pr_content_work_rules WHERE id = :id"), {"id": rule_id}
            )
            await connection.commit()
    finally:
        await engine.dispose()


# ===========================================================================
# 4-5: THE RACE
# ===========================================================================


async def _provision_and_record(database: Database, content_id: uuid.UUID) -> str:
    """One worker's run for one piece of an unseen content type.

    The three writes the projector's missing-binding path makes, in its order,
    in one transaction: the type, the binding, the result. Driven through the
    services rather than the projector so the race is on the writes and not on
    a content workflow this suite has no reason to build.
    """
    async with database.session() as session:
        services = build_pr_services(session, Settings(**SETTINGS_KWARGS))
        actor = system_actor()
        request_id = uuid.uuid4()
        try:
            work_type = await services.work.ensure_source_work_type(
                actor=actor,
                request_id=request_id,
                code=CODE,
                name=auto_work_type_name(KIND, TYPE),
                category=AUTO_WORK_TYPE_CATEGORIES[KIND],
                default_unit=AUTO_WORK_TYPE_UNIT,
                provenance={"reason": "content_auto_provision"},
            )
            rule = await services.content_work_rules.ensure_auto_rule(
                actor=actor,
                request_id=request_id,
                contribution_kind=KIND,
                content_type=TYPE,
                work_type=work_type,
            )
            await services.work_results.record_source_result(
                actor=actor,
                request_id=request_id,
                source_type=PrWorkResultSource.CONTENT,
                source_key=content_work_source_key(KIND, content_id),
                work_type_id=rule.work_type_id,
                subject_user_id=WRITER,
                occurred_at=SEPTEMBER,
                validated_by_user_id=HEAD,
                validated_at=SEPTEMBER,
                label=f"CNT-{content_id.hex[:6]}",
            )
            await session.commit()
            return "ok"
        except Exception as exc:  # pragma: no cover - a failure is the finding
            await session.rollback()
            return type(exc).__name__


async def test_04_two_workers_meeting_one_unseen_type_provision_it_once(
    database: Database,
) -> None:
    """Worker A creates the type; worker B races on the same content type.

    Both succeed. One type, one binding, two results - one per piece - and the
    writer's September stream holds both.
    """
    first, second = uuid.uuid4(), uuid.uuid4()
    outcomes = await asyncio.gather(
        _provision_and_record(database, first), _provision_and_record(database, second)
    )
    assert outcomes == ["ok", "ok"], outcomes

    async with database.session() as session:
        types = (
            (await session.execute(select(PrWorkType).where(PrWorkType.code == CODE)))
            .scalars()
            .all()
        )
        assert len(types) == 1, "one work type, however many workers met the type first"
        rules = (
            (
                await session.execute(
                    select(PrContentWorkRule).where(
                        PrContentWorkRule.contribution_kind == KIND,
                        PrContentWorkRule.content_type == TYPE,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rules) == 1 and rules[0].work_type_id == types[0].id
        assert rules[0].created_by_user_id is None
        results = (
            (
                await session.execute(
                    select(PrWorkResult).where(
                        PrWorkResult.source_type == PrWorkResultSource.CONTENT,
                        PrWorkResult.source_key.in_(
                            [content_work_source_key(KIND, one) for one in (first, second)]
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(results) == 2, "each piece is its own result"
        assert {one.status for one in results} == {PrWorkCountStatus.COUNTED}
        assert {one.work_item_id for one in results} == {results[0].work_item_id}
        container = await session.get(PrWorkItem, results[0].work_item_id)
        assert container is not None
        assert container.work_type_id == types[0].id
        assert container.subject_user_id == WRITER
        assert container.quantity == Decimal("2.00")


async def test_05_a_retry_after_the_race_reuses_everything(database: Database) -> None:
    """The same two pieces again, plus a third: no new type, no new rule, no new
    result for the replayed keys, one for the new piece."""
    third = uuid.uuid4()
    before = await _count_all(database)
    outcomes = await asyncio.gather(
        _provision_and_record(database, third),
        _provision_and_record(database, third),
        _provision_and_record(database, third),
    )
    assert outcomes == ["ok", "ok", "ok"], outcomes
    after = await _count_all(database)
    assert after["types"] == before["types"] == 1
    assert after["rules"] == before["rules"] == 1
    assert after["results"] == before["results"] + 1


async def _count_all(database: Database) -> dict[str, int]:
    async with database.session() as session:
        types = await session.scalar(
            text("SELECT count(*) FROM pr_work_types WHERE code = :code"), {"code": CODE}
        )
        rules = await session.scalar(
            text(
                "SELECT count(*) FROM pr_content_work_rules "
                "WHERE contribution_kind = :kind AND content_type = :type"
            ),
            {"kind": KIND.value, "type": TYPE.value},
        )
        results = await session.scalar(
            text("SELECT count(*) FROM pr_work_results WHERE source_type = 'CONTENT'")
        )
    return {"types": int(types or 0), "rules": int(rules or 0), "results": int(results or 0)}
