"""The work taxonomy against a real PostgreSQL. M2.5.

Three things only a real database can answer, and each is a rule the milestone
would otherwise be asserting about itself:

#. **the unique code is the database's**, not a ``SELECT`` the service does
   first. Two concurrent creates of the same code race past an application-level
   check and both insert; the partial unique index is what makes the second one
   fail (tests 1-2);
#. **a referenced type cannot be deleted**, whatever anybody asks. Every foreign
   key into ``pr_work_types`` is ``ON DELETE RESTRICT``, so history is preserved
   by the schema rather than by the absence of a route (tests 3-5);
#. **"in use" is computed from the real foreign keys** - a work item, a quota,
   or an allocation - and SQLite's looser behaviour would not prove it (tests
   6-8).

**No migration.** M2.5 changed no schema: ``pr_work_types`` already carried every
field the milestone needs, and the enum columns are ``VARCHAR`` with no CHECK, so
adding the ``ACCOUNT`` unit was a code change. Test 9 pins that, because "we did
not need one" is worth an assertion when the next person wonders why the
revision numbering skips M2.5.

Requires a PostgreSQL you are willing to have databases created on::

    export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/postgres
    uv run pytest tests/integration/test_pr_work_type_management.py -m integration

The fixture creates its own uniquely-named ``meobot_worktype_*`` database and
drops that one afterwards. Nothing else on the server is read or written.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import func, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from meobot.application.pr_services import build_pr_services
from meobot.application.pr_work_service import CreateWorkCommand
from meobot.core.config import Settings
from meobot.db.models.pr_work import PrWorkType
from meobot.db.session import Database
from meobot.domain.identity.models import Actor, Role
from meobot.domain.pr.errors import PrConflictError
from meobot.domain.pr.work import PrWorkCategory, PrWorkUnit
from meobot.domain.pr.work_quota import PrWorkQuotaBasis
from meobot.domain.pr.work_types import BOOTSTRAP_WORK_TYPES, WORK_TYPE_STRUCTURE_LOCKED
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
async def type_db() -> AsyncIterator[Database]:
    assert TEST_DATABASE_URL is not None
    base = make_url(TEST_DATABASE_URL)
    scratch = f"meobot_worktype_{uuid.uuid4().hex[:8]}"
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


class People:
    owner_id: uuid.UUID
    lead_id: uuid.UUID
    member_id: uuid.UUID

    @property
    def owner(self) -> Actor:
        return Actor(user_id=self.owner_id, full_name="Chị Chủ", role=Role.OWNER)

    @property
    def lead(self) -> Actor:
        return Actor(user_id=self.lead_id, full_name="Lê Trưởng Nhóm", role=Role.TEAM_LEAD)

    @property
    def member(self) -> Actor:
        return Actor(user_id=self.member_id, full_name="Phương Nhung", role=Role.EMPLOYEE)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def people(type_db: Database) -> People:
    from meobot.db.models.user import User

    world = People()
    async with type_db.session() as session:
        rows = {
            "owner": User(full_name="Chị Chủ", role=Role.OWNER),
            "lead": User(full_name="Lê Trưởng Nhóm", role=Role.TEAM_LEAD),
            "member": User(full_name="Phương Nhung", role=Role.EMPLOYEE),
        }
        session.add_all(list(rows.values()))
        await session.flush()
        world.owner_id = rows["owner"].id
        world.lead_id = rows["lead"].id
        world.member_id = rows["member"].id
        await session.commit()
    return world


async def make_type(type_db: Database, people: People, **kwargs) -> uuid.UUID:
    """One committed work type."""
    async with type_db.session() as session:
        services = build_pr_services(session, Settings(**SETTINGS_KWARGS))
        row = await services.work.create_work_type(
            actor=people.owner,
            request_id=uuid.uuid4(),
            code=kwargs.pop("code", f"CODE_{uuid.uuid4().hex[:8].upper()}"),
            name=kwargs.pop("name", "Loại việc"),
            category=kwargs.pop("category", PrWorkCategory.CONTENT),
            **kwargs,
        )
        await session.commit()
        return row.id


# ===========================================================================
# 1-2: THE UNIQUE CODE IS THE DATABASE'S
# ===========================================================================


async def test_01_the_service_refuses_a_duplicate_code(type_db: Database, people: People) -> None:
    await make_type(type_db, people, code="SHORT_SCRIPT")
    with pytest.raises(PrConflictError) as caught:
        await make_type(type_db, people, code="SHORT_SCRIPT")
    assert caught.value.details["reason"] == "duplicate_code"


async def test_02_the_unique_index_refuses_one_the_service_never_saw(
    type_db: Database, people: People
) -> None:
    """The guarantee under the service's ``SELECT``.

    Two concurrent creates both pass an application-level "is this code taken"
    check and both insert. Written here as a direct insert because that is what
    the losing transaction's statement amounts to.
    """
    async with type_db.session() as session:
        session.add(
            PrWorkType(
                code="SHORT_SCRIPT",
                name="Bản sao",
                category=PrWorkCategory.CONTENT,
                default_unit=PrWorkUnit.ITEM,
                default_quota_basis=PrWorkQuotaBasis.ITEM_COUNT,
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()


# ===========================================================================
# 3-5: HISTORY IS PRESERVED BY THE SCHEMA
# ===========================================================================


async def test_03_every_reference_into_the_taxonomy_restricts(
    type_db: Database,
) -> None:
    """``RESTRICT`` on all of them, so no path deletes a type out from under
    the rows that name it - including a path somebody adds later."""
    async with type_db.session() as session:
        connection = await session.connection()

        def read(sync_connection):  # type: ignore[no-untyped-def]
            inspector = inspect(sync_connection)
            found = {}
            for table in inspector.get_table_names():
                for key in inspector.get_foreign_keys(table):
                    if key["referred_table"] == "pr_work_types":
                        found[table] = key["options"].get("ondelete")
            return found

        rules = await connection.run_sync(read)

    assert {"pr_work_items", "pr_work_quotas", "pr_work_quota_allocations"} <= set(rules)
    assert set(rules.values()) == {"RESTRICT"}, rules


async def test_04_a_referenced_type_cannot_be_deleted(type_db: Database, people: People) -> None:
    """The no-delete rule, enforced where a stray ``DELETE`` would land."""
    type_id = await make_type(type_db, people, code="USED_TYPE")
    async with type_db.session() as session:
        services = build_pr_services(session, Settings(**SETTINGS_KWARGS))
        await services.work.assign_work(
            actor=people.lead,
            request_id=uuid.uuid4(),
            command=CreateWorkCommand(
                work_type_id=type_id,
                title="Việc thật",
                contributor_user_ids=(people.member_id,),
            ),
        )
        await session.commit()

    async with type_db.session() as session:
        with pytest.raises(IntegrityError):
            await session.execute(text("DELETE FROM pr_work_types WHERE id = :id"), {"id": type_id})
            await session.flush()
        await session.rollback()

    async with type_db.session() as session:
        assert await session.get(PrWorkType, type_id) is not None


async def test_05_deactivating_keeps_the_row_and_its_work(
    type_db: Database, people: People
) -> None:
    type_id = await make_type(type_db, people, code="RETIRED_TYPE")
    async with type_db.session() as session:
        services = build_pr_services(session, Settings(**SETTINGS_KWARGS))
        await services.work.assign_work(
            actor=people.lead,
            request_id=uuid.uuid4(),
            command=CreateWorkCommand(
                work_type_id=type_id,
                title="Việc cũ",
                contributor_user_ids=(people.member_id,),
            ),
        )
        await services.work.set_work_type_active(
            actor=people.owner,
            request_id=uuid.uuid4(),
            work_type_id=type_id,
            is_active=False,
        )
        await session.commit()

    async with type_db.session() as session:
        row = await session.get(PrWorkType, type_id)
        assert row is not None and row.is_active is False
        work = await session.scalar(
            text("SELECT count(*) FROM pr_work_items WHERE work_type_id = :id"),  # type: ignore[arg-type]
            {"id": type_id},
        )
        assert work == 1, "the work filed under it is untouched"


# ===========================================================================
# 6-8: "IN USE", AGAINST THE REAL FOREIGN KEYS
# ===========================================================================


async def test_06_an_unused_type_is_editable_end_to_end(type_db: Database, people: People) -> None:
    type_id = await make_type(type_db, people, code="FRESH_TYPE")
    async with type_db.session() as session:
        services = build_pr_services(session, Settings(**SETTINGS_KWARGS))
        assert await services.work.is_work_type_in_use(type_id) is False
        updated = await services.work.update_work_type(
            actor=people.owner,
            request_id=uuid.uuid4(),
            work_type_id=type_id,
            code="FRESH_RENAMED",
            default_quota_basis=PrWorkQuotaBasis.QUANTITY,
            default_unit=PrWorkUnit.ACCOUNT,
        )
        assert updated.code == "FRESH_RENAMED"
        assert updated.default_unit is PrWorkUnit.ACCOUNT
        await session.commit()


async def test_07_a_work_item_locks_the_structure(type_db: Database, people: People) -> None:
    type_id = await make_type(type_db, people, code="LOCKED_BY_ITEM")
    async with type_db.session() as session:
        services = build_pr_services(session, Settings(**SETTINGS_KWARGS))
        await services.work.assign_work(
            actor=people.lead,
            request_id=uuid.uuid4(),
            command=CreateWorkCommand(
                work_type_id=type_id,
                title="Việc",
                contributor_user_ids=(people.member_id,),
            ),
        )
        await session.commit()

    async with type_db.session() as session:
        services = build_pr_services(session, Settings(**SETTINGS_KWARGS))
        assert await services.work.is_work_type_in_use(type_id) is True
        with pytest.raises(PrConflictError) as caught:
            await services.work.update_work_type(
                actor=people.owner,
                request_id=uuid.uuid4(),
                work_type_id=type_id,
                default_quota_basis=PrWorkQuotaBasis.QUANTITY,
                default_unit=PrWorkUnit.COMMENT,
            )
        assert caught.value.details["reason"] == WORK_TYPE_STRUCTURE_LOCKED


async def test_08_a_quota_alone_locks_the_structure(type_db: Database, people: People) -> None:
    """The case a work-item-only rule would miss.

    A plan approved in March sets a target on a type nobody has filed against
    yet. Its basis is what the approved quota was written under, so the type is
    in use even though ``pr_work_items`` is empty for it.
    """
    type_id = await make_type(type_db, people, code="LOCKED_BY_QUOTA")
    async with type_db.session() as session:
        services = build_pr_services(session, Settings(**SETTINGS_KWARGS))
        period = await services.work_periods.ensure_month_period(
            actor=people.owner, request_id=uuid.uuid4(), year=2026, month=9
        )
        plan = await services.work_plans.create_plan(
            actor=people.owner,
            request_id=uuid.uuid4(),
            user_id=people.member_id,
            period_id=period.id,
        )
        await services.work_plans.add_quota(
            actor=people.owner,
            request_id=uuid.uuid4(),
            plan_id=plan.plan.id,
            work_type_id=type_id,
            target_value=Decimal("5"),
            eligibility_cap=Decimal("5"),
        )
        await session.commit()

    async with type_db.session() as session:
        services = build_pr_services(session, Settings(**SETTINGS_KWARGS))
        items = await session.scalar(
            text("SELECT count(*) FROM pr_work_items WHERE work_type_id = :id"),  # type: ignore[arg-type]
            {"id": type_id},
        )
        assert items == 0, "no work has been filed under it"
        assert await services.work.is_work_type_in_use(type_id) is True


# ===========================================================================
# 9-10: THE SCHEMA M2.5 DID NOT CHANGE, AND THE BOOTSTRAP
# ===========================================================================


async def test_09_the_enum_columns_carry_no_check_constraint(
    type_db: Database,
) -> None:
    """Why M2.5 needed no migration to add the ``ACCOUNT`` unit.

    The repository stores enums as ``VARCHAR`` with no database CHECK - the
    convention 0032 writes down - so extending one is a code change. If this
    ever stops being true, adding a unit becomes a migration and this test is
    what says so.
    """
    async with type_db.session() as session:
        constraints = await session.scalar(
            text(
                "SELECT count(*) FROM information_schema.check_constraints c "
                "JOIN information_schema.constraint_column_usage u "
                "  ON c.constraint_name = u.constraint_name "
                "WHERE u.table_name = 'pr_work_types' "
                "  AND u.column_name IN ('default_unit', 'default_quota_basis', 'category')"
            )
        )
    assert constraints == 0


async def test_10_bootstrap_is_idempotent_against_the_unique_index(
    type_db: Database, people: People
) -> None:
    """Run twice in two separate transactions, which is the way an operator
    actually double-presses it."""
    async with type_db.session() as session:
        services = build_pr_services(session, Settings(**SETTINGS_KWARGS))
        first = await services.work.bootstrap_work_types(
            actor=people.owner, request_id=uuid.uuid4()
        )
        await session.commit()
    assert len(first) == len(BOOTSTRAP_WORK_TYPES)

    async with type_db.session() as session:
        services = build_pr_services(session, Settings(**SETTINGS_KWARGS))
        second = await services.work.bootstrap_work_types(
            actor=people.owner, request_id=uuid.uuid4()
        )
        await session.commit()
    assert second == ()

    async with type_db.session() as session:
        total = await session.scalar(
            select(func.count())
            .select_from(PrWorkType)
            .where(PrWorkType.code.in_([spec.code for spec in BOOTSTRAP_WORK_TYPES]))
        )
    assert total == len(BOOTSTRAP_WORK_TYPES)
