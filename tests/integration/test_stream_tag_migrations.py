"""``0048`` against a real PostgreSQL: every active account still untagged is tagged PR.

* **only the untagged active accounts** - an active user with no
  ``org_unit_members`` row gets one open PR ``MEMBER`` row with the
  deterministic id; an inactive one, an ORD-only one and one whose PR tag was
  closed are left exactly as they were;
* **nobody created afterwards** - a user inserted at head stays untagged;
* **the migration goes both ways** - down to ``0047`` deletes exactly the rows
  ``0048`` wrote (and none written by a person); up again writes them back.

Requires a PostgreSQL you are willing to have databases created on::

    export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/postgres
    uv run pytest tests/integration/test_stream_tag_migrations.py -m integration
"""

from __future__ import annotations

import importlib.util
import uuid
from collections.abc import AsyncIterator
from types import ModuleType

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from meobot.core.config import Settings
from meobot.db.session import Database
from meobot.domain.units.models import UnitCode, unit_seed_id
from tests.integration.legacy_rows import insert_legacy_user
from tests.integration.test_dispatch_migrations import (
    ROOT,
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
PR = unit_seed_id(UnitCode.PR)
ADS = unit_seed_id(UnitCode.ADS)

_ids: dict[str, uuid.UUID] = {}


def _migration() -> ModuleType:
    path = ROOT / "alembic" / "versions" / "0048_tag_untagged_users_pr.py"
    spec = importlib.util.spec_from_file_location("migration_0048", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _rows(dsn: str, sql: str, **params: object) -> list[tuple[object, ...]]:
    engine = create_async_engine(dsn)
    try:
        async with engine.connect() as connection:
            return [tuple(row) for row in (await connection.execute(text(sql), params)).all()]
    finally:
        await engine.dispose()


async def _tags(dsn: str, user_id: uuid.UUID) -> list[tuple[object, ...]]:
    return await _rows(
        dsn,
        "SELECT id, unit_id, role, is_lead, left_at IS NULL AS open "
        "FROM org_unit_members WHERE user_id = :id ORDER BY unit_id",
        id=user_id,
    )


async def _tag(
    session: object, user_id: uuid.UUID, unit_id: uuid.UUID, role: str, *, closed: bool = False
) -> uuid.UUID:
    row_id = uuid.uuid4()
    await session.execute(  # type: ignore[attr-defined]
        text(
            "INSERT INTO org_unit_members "
            "(id, unit_id, user_id, role, is_lead, joined_at, left_at) "
            "VALUES (:id, :unit, :user, :role, false, now(), "
            "CASE WHEN :closed THEN now() ELSE NULL END)"
        ),
        {"id": row_id, "unit": unit_id, "user": user_id, "role": role, "closed": closed},
    )
    return row_id


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def dsn() -> AsyncIterator[str]:
    """A scratch database at ``0047`` with five kinds of account, then head."""
    assert TEST_DATABASE_URL is not None
    base = make_url(TEST_DATABASE_URL)
    scratch = f"meobot_0048_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(
        base.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=None
    )
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{scratch}"'))
        url = base.set(database=scratch).render_as_string(hide_password=False)
        await upgrade_to(url, "0047")
        database = Database(Settings(**SETTINGS_KWARGS, database_url=url))  # type: ignore[arg-type]
        try:
            async with database.session() as session:
                for key in ("untagged", "untagged_lead", "inactive", "ads_only", "closed", "pr"):
                    role = "TEAM_LEAD" if key == "untagged_lead" else "EMPLOYEE"
                    _ids[key] = await insert_legacy_user(session, full_name=key, role=role)
                await session.execute(
                    text("UPDATE users SET active = false WHERE id = :id"), {"id": _ids["inactive"]}
                )
                _ids["ads_tag"] = await _tag(session, _ids["ads_only"], ADS, "ORDERER")
                _ids["closed_tag"] = await _tag(session, _ids["closed"], PR, "MEMBER", closed=True)
                _ids["pr_tag"] = await _tag(session, _ids["pr"], PR, "MEMBER")
                await session.commit()
        finally:
            await database.dispose()
        await upgrade_to(url)
        yield url
    finally:
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)'))
        await admin.dispose()


async def test_01_the_head_is_the_newest_revision(dsn: str) -> None:
    assert await _rows(dsn, "SELECT version_num FROM alembic_version") == [(alembic_head(),)]


async def test_02_the_untagged_active_accounts_are_tagged_pr(dsn: str) -> None:
    migration = _migration()
    for key in ("untagged", "untagged_lead"):
        assert await _tags(dsn, _ids[key]) == [
            (migration.seeded_tag_id(_ids[key]), PR, "MEMBER", False, True)
        ]
    # The ids the migration and the model agree on.
    assert migration.pr_unit_id() == PR


async def test_03_everybody_else_is_left_alone(dsn: str) -> None:
    assert await _tags(dsn, _ids["inactive"]) == []
    assert await _tags(dsn, _ids["ads_only"]) == [(_ids["ads_tag"], ADS, "ORDERER", False, True)]
    assert await _tags(dsn, _ids["closed"]) == [(_ids["closed_tag"], PR, "MEMBER", False, False)]
    assert await _tags(dsn, _ids["pr"]) == [(_ids["pr_tag"], PR, "MEMBER", False, True)]
    total = await _rows(dsn, "SELECT count(*) FROM org_unit_members")
    assert total == [(5,)]


async def test_04_a_user_created_afterwards_stays_untagged(dsn: str) -> None:
    database = Database(Settings(**SETTINGS_KWARGS, database_url=dsn))  # type: ignore[arg-type]
    try:
        async with database.session() as session:
            _ids["later"] = await insert_legacy_user(session, full_name="Later", role="EMPLOYEE")
            await session.commit()
    finally:
        await database.dispose()
    assert await _tags(dsn, _ids["later"]) == []


async def test_05_the_migration_goes_both_ways(dsn: str) -> None:
    migration = _migration()
    # A person re-tags one of the seeded accounts in ORD too: not 0048's row.
    database = Database(Settings(**SETTINGS_KWARGS, database_url=dsn))  # type: ignore[arg-type]
    try:
        async with database.session() as session:
            _ids["manual"] = await _tag(session, _ids["untagged"], ADS, "DUNG")
            await session.commit()
    finally:
        await database.dispose()

    await downgrade_to(dsn, "0047")
    assert await _rows(dsn, "SELECT version_num FROM alembic_version") == [("0047",)]
    assert await _tags(dsn, _ids["untagged"]) == [(_ids["manual"], ADS, "DUNG", False, True)]
    assert await _tags(dsn, _ids["untagged_lead"]) == []
    # Every tag a person wrote survives.
    assert await _tags(dsn, _ids["pr"]) == [(_ids["pr_tag"], PR, "MEMBER", False, True)]
    assert await _tags(dsn, _ids["closed"]) == [(_ids["closed_tag"], PR, "MEMBER", False, False)]
    assert await _tags(dsn, _ids["ads_only"]) == [(_ids["ads_tag"], ADS, "ORDERER", False, True)]

    await upgrade_to(dsn)
    assert await _rows(dsn, "SELECT version_num FROM alembic_version") == [(alembic_head(),)]
    # "untagged" now has a row (the manual ORD one), so it is not re-tagged;
    # "untagged_lead" and "later" have none and are.
    assert await _tags(dsn, _ids["untagged"]) == [(_ids["manual"], ADS, "DUNG", False, True)]
    for key in ("untagged_lead", "later"):
        assert await _tags(dsn, _ids[key]) == [
            (migration.seeded_tag_id(_ids[key]), PR, "MEMBER", False, True)
        ]
