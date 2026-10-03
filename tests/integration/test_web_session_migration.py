"""Migration ``0017`` against a real PostgreSQL.

Skipped unless ``MEOBOT_TEST_DATABASE_URL`` points at a database you are willing
to have dropped and rebuilt::

    docker run --rm -d --name pg-check \\
      -e POSTGRES_USER=meobot -e POSTGRES_PASSWORD=t -e POSTGRES_DB=meobot_test \\
      -p 127.0.0.1:55440:5432 postgres:16-alpine
    MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:t@127.0.0.1:55440/meobot_test \\
      uv run pytest tests/integration/test_web_session_migration.py -q

Why this exists separately from the unit tests
----------------------------------------------

``tests/unit/test_pr_web_admin.py`` builds its schema from ORM metadata on SQLite.
That proves behaviour and proves nothing about the migration - the two are written
independently, and the failure this file catches is them **disagreeing**: a column
the model has and 0017 does not, a nullability that differs, or a constraint whose
name the naming convention mangles.

That last one is not hypothetical. The check constraints in this migration were
first written as ``ck_web_sessions_token_hash_not_empty``, and
``NAMING_CONVENTION`` prefixed them again - producing
``ck_web_sessions_ck_web_sessions_token_hash_not_empty``, which no ORM-generated
schema would ever match. Only a real PostgreSQL shows that.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from meobot.db.models.web_session import WebSession, WebSessionKind

TEST_DATABASE_URL = os.environ.get("MEOBOT_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="Set MEOBOT_TEST_DATABASE_URL to run migration checks."
)


@pytest.mark.asyncio
async def test_the_table_matches_the_model() -> None:
    """Every mapped column exists, with the nullability the model declares.

    Compared against ``WebSession.__table__`` rather than a hand-written list, so
    a column added to the model without a migration fails here instead of at
    runtime on the NAS.
    """
    engine = create_async_engine(str(TEST_DATABASE_URL))
    try:
        async with engine.connect() as connection:
            rows = (
                await connection.execute(
                    text(
                        "SELECT column_name, is_nullable FROM information_schema.columns "
                        "WHERE table_name = 'web_sessions'"
                    )
                )
            ).all()
    finally:
        await engine.dispose()

    assert rows, "web_sessions is missing - run `alembic upgrade head` first."
    live = {name: nullable == "YES" for name, nullable in rows}
    for column in WebSession.__table__.columns:
        assert column.name in live, f"{column.name} is in the model but not in 0017."
        assert live[column.name] == column.nullable, column.name
    assert set(live) == {column.name for column in WebSession.__table__.columns}


@pytest.mark.asyncio
async def test_the_constraints_are_named_and_shaped_as_intended() -> None:
    """The names, the RESTRICT, and the unique index on the hash.

    The unique index is the one that matters: two subjects sharing a credential is
    the failure it prevents, and an index that was created non-unique would be
    invisible until it mattered.
    """
    engine = create_async_engine(str(TEST_DATABASE_URL))
    try:
        async with engine.connect() as connection:
            # ``contype`` comes back as a one-byte ``"char"``, which asyncpg
            # hands over as ``bytes``. Decoded here so the assertions below read
            # as the letters PostgreSQL documents.
            constraints = {
                name: kind.decode() if isinstance(kind, bytes) else kind
                for name, kind in (
                    await connection.execute(
                        text(
                            "SELECT conname, contype FROM pg_constraint "
                            "WHERE conrelid = 'web_sessions'::regclass"
                        )
                    )
                ).all()
            }
            indexes = dict(
                (
                    await connection.execute(
                        text(
                            "SELECT indexname, indexdef FROM pg_indexes "
                            "WHERE tablename = 'web_sessions'"
                        )
                    )
                ).all()
            )
            delete_rule = (
                await connection.execute(
                    text(
                        "SELECT rc.delete_rule FROM information_schema.referential_constraints rc "
                        "WHERE rc.constraint_name = 'fk_web_sessions_user_id_users'"
                    )
                )
            ).scalar_one()
    finally:
        await engine.dispose()

    # Single-prefixed, as ``NAMING_CONVENTION`` produces from a bare name.
    assert constraints.get("ck_web_sessions_token_hash_not_empty") == "c"
    assert constraints.get("ck_web_sessions_expiry_after_creation") == "c"
    assert constraints.get("pk_web_sessions") == "p"
    assert constraints.get("fk_web_sessions_user_id_users") == "f"
    assert not any(name.startswith("ck_web_sessions_ck_") for name in constraints), constraints

    assert "UNIQUE" in indexes["uq_web_sessions_token_hash"]
    assert "ix_web_sessions_user_kind" in indexes
    # Deleting somebody who has signed in must fail, not discard the record.
    assert delete_rule == "RESTRICT"


@pytest.mark.asyncio
async def test_the_kind_column_stores_values_and_enforces_nothing() -> None:
    """``kind`` is ``VARCHAR``, not a PostgreSQL ENUM - and has no CHECK.

    The same honest note every PR step carries: ``value_enum`` with
    ``native_enum=False`` creates no type and, since SQLAlchemy 1.4, no
    vocabulary CHECK either. The vocabulary is enforced in Python. Asserting the
    absence keeps the documentation from drifting into claiming database-level
    enforcement that is not there.
    """
    engine = create_async_engine(str(TEST_DATABASE_URL))
    try:
        async with engine.connect() as connection:
            data_type = (
                await connection.execute(
                    text(
                        "SELECT data_type FROM information_schema.columns "
                        "WHERE table_name = 'web_sessions' AND column_name = 'kind'"
                    )
                )
            ).scalar_one()
            checks = (
                (
                    await connection.execute(
                        text(
                            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                            "WHERE conrelid = 'web_sessions'::regclass AND contype = 'c'"
                        )
                    )
                )
                .scalars()
                .all()
            )
    finally:
        await engine.dispose()

    assert data_type == "character varying"
    joined = " ".join(checks)
    for value in (kind.value for kind in WebSessionKind):
        assert value not in joined, "The stored vocabulary is a Python rule, not a CHECK."
