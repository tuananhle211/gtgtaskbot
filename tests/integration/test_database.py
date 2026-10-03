"""Integration tests against a real PostgreSQL.

Skipped unless ``MEOBOT_TEST_DATABASE_URL`` points at a database you are willing
to have migrated. On the NAS::

    docker compose run --rm \\
      -e MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@postgres:5432/meobot_test \\
      api pytest tests/integration -m integration

These never run in the default ``pytest`` invocation, so ``make test`` stays
offline and side-effect free.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import func, select

from meobot.application.audit_service import AuditService
from meobot.application.script_type_service import ScriptTypeService
from meobot.core.config import Settings
from meobot.db.models.audit_log import AuditLog
from meobot.db.models.script_type import ScriptType
from meobot.db.session import Database
from meobot.domain.audit.models import AuditResult
from meobot.domain.identity.models import Actor, Role
from meobot.domain.script_types.rubric import TOTAL_WEIGHT

TEST_DATABASE_URL = os.environ.get("MEOBOT_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not TEST_DATABASE_URL,
        reason="Set MEOBOT_TEST_DATABASE_URL to run integration tests",
    ),
]

SEEDED_CODES = {
    "doctor_education",
    "emotional_story",
    "short_drama",
    "viral_discussion",
    "service_ad",
}


@pytest.fixture
async def database():  # type: ignore[no-untyped-def]
    """A Database bound to the integration DSN, disposed after the test."""
    assert TEST_DATABASE_URL is not None
    db = Database(Settings(_env_file=None, database_url=TEST_DATABASE_URL))
    try:
        yield db
    finally:
        await db.dispose()


async def test_database_answers(database: Database) -> None:
    assert await database.ping() is True


async def test_migrations_created_the_seeded_script_types(database: Database) -> None:
    """Requires `alembic upgrade head` to have run against this database."""
    async with database.session() as session:
        result = await session.execute(select(ScriptType.code))
        codes = set(result.scalars().all())
    assert codes >= SEEDED_CODES


async def test_seeded_rubrics_are_valid_in_the_database(database: Database) -> None:
    async with database.session() as session:
        service = ScriptTypeService(session, AuditService(session))
        script_types = await service.list_script_types()
        for script_type in script_types:
            if script_type.code not in SEEDED_CODES:
                continue
            rubric = await service.get_rubric(script_type.id)
            assert rubric.total_weight == TOTAL_WEIGHT, script_type.code


async def test_audit_log_round_trip(database: Database) -> None:
    """A write plus its audit entry commit together and read back."""
    request_id = uuid.uuid4()
    actor = Actor(user_id=None, telegram_user_id=1, full_name="integration", role=Role.OWNER)

    async with database.transaction() as session:
        await AuditService(session).record_action(
            request_id=request_id,
            actor=actor,
            action="integration.test",
            result=AuditResult.SUCCESS,
            entity_type="test",
            entity_id=str(request_id),
            after_data={"note": "hello", "api_key": "should-be-redacted"},
        )

    async with database.session() as session:
        result = await session.execute(select(AuditLog).where(AuditLog.request_id == request_id))
        row = result.scalar_one()

    assert row.action == "integration.test"
    assert row.after_data is not None
    assert row.after_data["note"] == "hello"
    assert row.after_data["api_key"] != "should-be-redacted"


async def test_duplicate_script_type_version_is_rejected_by_the_constraint(
    database: Database,
) -> None:
    """The (script_type_id, version) unique constraint really exists."""
    from sqlalchemy.exc import IntegrityError

    from meobot.db.models.script_type import ScriptTypeVersion

    async with database.session() as session:
        result = await session.execute(
            select(ScriptType).where(ScriptType.code == "doctor_education")
        )
        script_type = result.scalar_one()
        existing_version = script_type.current_version

    with pytest.raises(IntegrityError):
        async with database.transaction() as session:
            session.add(
                ScriptTypeVersion(
                    script_type_id=script_type.id,
                    version=existing_version,
                    configuration={},
                    review_rubric={"criteria": [], "passing_score": 0},
                )
            )


async def test_audit_table_is_append_only_in_practice(database: Database) -> None:
    """Sanity check that the table accumulates rather than replaces."""
    async with database.session() as session:
        before = (await session.execute(select(func.count()).select_from(AuditLog))).scalar_one()

    async with database.transaction() as session:
        await AuditService(session).record_action(
            request_id=uuid.uuid4(),
            actor=Actor(user_id=None, telegram_user_id=1, full_name="x", role=Role.OWNER),
            action="integration.count",
            result=AuditResult.SUCCESS,
        )

    async with database.session() as session:
        after = (await session.execute(select(func.count()).select_from(AuditLog))).scalar_one()

    assert after == before + 1
