"""``0047`` against a real PostgreSQL: ``user_avatars``, the roundtrip.

* **the models describe the migration** - ``compare_metadata`` finds nothing
  about ``user_avatars`` at head;
* **existing rows are untouched** - a user from before ``0047`` simply has no
  picture;
* **the constraints hold** - type, size range, version, one row per person,
  and deleting the user deletes the picture;
* **the migration goes both ways** - down to ``0046`` drops the table and keeps
  every user; up again restores it, empty.

Requires a PostgreSQL you are willing to have databases created on::

    export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/postgres
    uv run pytest tests/integration/test_avatar_migrations.py -m integration
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

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

SETTINGS_KWARGS = {"_env_file": None, "web_cookie_secure": False}
TABLE = "user_avatars"
PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(32))

_ids: dict[str, uuid.UUID] = {}


async def _scalar(dsn: str, sql: str, **params: object) -> object:
    engine = create_async_engine(dsn)
    try:
        async with engine.connect() as connection:
            return await connection.scalar(text(sql), params)
    finally:
        await engine.dispose()


async def _table_exists(dsn: str) -> bool:
    return bool(await _scalar(dsn, "SELECT to_regclass(:name) IS NOT NULL", name=TABLE))


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def dsn() -> AsyncIterator[str]:
    """A scratch database at ``0046`` with one user, then head."""
    assert TEST_DATABASE_URL is not None
    base = make_url(TEST_DATABASE_URL)
    scratch = f"meobot_0047_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(
        base.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=None
    )
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{scratch}"'))
        url = base.set(database=scratch).render_as_string(hide_password=False)
        await upgrade_to(url, "0046")
        assert not await _table_exists(url)
        database = Database(Settings(**SETTINGS_KWARGS, database_url=url))  # type: ignore[arg-type]
        try:
            async with database.session() as session:
                _ids["old"] = await insert_legacy_user(session, full_name="Old", role="EMPLOYEE")
                _ids["gone"] = await insert_legacy_user(session, full_name="Gone", role="EMPLOYEE")
                await session.commit()
        finally:
            await database.dispose()
        await upgrade_to(url)
        yield url
    finally:
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)'))
        await admin.dispose()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def database(dsn: str) -> AsyncIterator[Database]:
    db = Database(Settings(**SETTINGS_KWARGS, database_url=dsn))  # type: ignore[arg-type]
    try:
        yield db
    finally:
        await db.dispose()


async def _insert(
    database: Database,
    user_id: uuid.UUID,
    *,
    content_type: str = "image/png",
    data: bytes = PNG,
    size: int | None = None,
    version: int | None = None,
) -> None:
    columns = "user_id, content_type, data, size_bytes" + (", version" if version else "")
    values = ":user_id, :content_type, :data, :size" + (", :version" if version else "")
    async with database.session() as session:
        await session.execute(
            text(f"INSERT INTO {TABLE} ({columns}) VALUES ({values})"),  # noqa: S608
            {
                "user_id": user_id,
                "content_type": content_type,
                "data": data,
                "size": len(data) if size is None else size,
                "version": version,
            },
        )
        await session.commit()


async def test_01_models_and_migration_agree(database: Database) -> None:
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    def _compare(sync_connection):  # type: ignore[no-untyped-def]
        context = MigrationContext.configure(
            sync_connection, opts={"compare_type": True, "compare_server_default": True}
        )
        return list(compare_metadata(context, Base.metadata))

    async with database.engine.connect() as connection:
        differences = await connection.run_sync(_compare)
    ours = [one for one in differences if TABLE in str(one)]
    assert ours == [], ours


async def test_02_the_head_is_the_newest_revision(database: Database) -> None:
    async with database.session() as session:
        head = await session.scalar(text("SELECT version_num FROM alembic_version"))
    assert head == alembic_head()


async def test_03_existing_users_have_no_picture(database: Database) -> None:
    async with database.session() as session:
        assert await session.scalar(text(f"SELECT count(*) FROM {TABLE}")) == 0  # noqa: S608
        assert await session.scalar(text("SELECT count(*) FROM users")) == 2


async def test_04_defaults_and_constraints(database: Database) -> None:
    await _insert(database, _ids["old"])
    async with database.session() as session:
        row = (
            await session.execute(
                text(f"SELECT content_type, data, size_bytes, version, updated_at FROM {TABLE}")  # noqa: S608
            )
        ).one()
    assert (row.content_type, bytes(row.data), row.size_bytes, row.version) == (
        "image/png",
        PNG,
        len(PNG),
        1,
    )
    assert row.updated_at is not None

    bad = [
        {"content_type": "image/gif"},
        {"content_type": "text/html"},
        {"size": 0},
        {"size": 300 * 1024 + 1},
        {"version": -1},
    ]
    for overrides in bad:
        with pytest.raises(IntegrityError):
            await _insert(database, _ids["gone"], **overrides)  # type: ignore[arg-type]
    # The upper bound itself is fine.
    await _insert(database, _ids["gone"], size=300 * 1024)
    # One row per person.
    with pytest.raises(IntegrityError):
        await _insert(database, _ids["old"])
    # A picture for nobody is refused.
    with pytest.raises(IntegrityError):
        await _insert(database, uuid.uuid4())


async def test_05_deleting_the_user_deletes_the_picture(database: Database) -> None:
    async with database.session() as session:
        # 0048 tagged the account PR; a tag row keeps its user, so it goes first.
        await session.execute(
            text("DELETE FROM org_unit_members WHERE user_id = :id"), {"id": _ids["gone"]}
        )
        await session.execute(text("DELETE FROM users WHERE id = :id"), {"id": _ids["gone"]})
        await session.commit()
        remaining = await session.scalar(
            text(f"SELECT count(*) FROM {TABLE} WHERE user_id = :id"),  # noqa: S608
            {"id": _ids["gone"]},
        )
    assert remaining == 0


async def test_06_the_migration_goes_both_ways(dsn: str) -> None:
    await downgrade_to(dsn, "0046")
    assert await _scalar(dsn, "SELECT version_num FROM alembic_version") == "0046"
    assert not await _table_exists(dsn)
    assert await _scalar(dsn, "SELECT full_name FROM users WHERE id = :id", id=_ids["old"]) == "Old"

    await upgrade_to(dsn)
    assert await _scalar(dsn, "SELECT version_num FROM alembic_version") == alembic_head()
    assert await _table_exists(dsn)
    assert await _scalar(dsn, f"SELECT count(*) FROM {TABLE}") == 0  # noqa: S608
