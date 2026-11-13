"""0105: Tài chính đợt 1 - the one CRM run's tables, the payment columns, the FINANCE grants.

Runs the real migrations on a scratch PostgreSQL database: the new columns
and table match the models, and a downgrade removes them.

    export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/postgres
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

import meobot.db.models  # noqa: F401 - registers every model for compare_metadata
from meobot.core.config import Settings
from meobot.db.base import Base
from meobot.db.session import Database
from tests.integration.test_dispatch_migrations import TEST_DATABASE_URL, downgrade_to, upgrade_to

#: finance_fees columns added later (0109, the "BCLT new" marks).
LATER_FEE_COLUMNS = ("status2", "customer", "status", "upsale", "category")


def _later(item: object) -> bool:
    text_ = repr(item)
    return "'finance_fees'" in text_ and any(
        f"Column('{name}'" in text_ for name in LATER_FEE_COLUMNS
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


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def dsn() -> AsyncIterator[str]:
    assert TEST_DATABASE_URL is not None
    base = make_url(TEST_DATABASE_URL)
    scratch = f"meobot_0105_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(
        base.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=None
    )
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{scratch}"'))
        url = base.set(database=scratch).render_as_string(hide_password=False)
        await upgrade_to(url, "0104")
        yield url
    finally:
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)'))
        await admin.dispose()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def database(dsn: str) -> AsyncIterator[Database]:
    db = Database(Settings(**SETTINGS_KWARGS, database_url=dsn))
    try:
        yield db
    finally:
        await db.dispose()


async def test_01_tables_columns_and_grants(dsn: str, database: Database) -> None:
    async with database.engine.begin() as connection:
        for code in ("KT", "BOD"):
            await connection.execute(
                text(
                    "INSERT INTO departments (id, code, name, function, active, created_at, "
                    "updated_at) VALUES (:id, :code, :code, 'NONE', true, now(), now())"
                ),
                {"id": uuid.uuid4(), "code": code},
            )
    await database.engine.dispose()
    await upgrade_to(dsn, "0105")

    def _diff(connection: object) -> list[object]:
        from alembic.autogenerate import compare_metadata
        from alembic.migration import MigrationContext

        context = MigrationContext.configure(
            connection,  # type: ignore[arg-type]
            opts={"compare_type": True, "compare_server_default": True},
        )
        return [item for item in compare_metadata(context, Base.metadata) if "crm_" in repr(item)]

    async with database.engine.begin() as connection:
        assert await connection.run_sync(_diff) == []
        grants = (
            await connection.execute(
                text(
                    "SELECT d.code, p.position, p.permission FROM department_permissions p "
                    "JOIN departments d ON d.id = p.department_id WHERE p.module = 'FINANCE' "
                    "ORDER BY 1, 2, 3"
                )
            )
        ).all()
        developing = await connection.scalar(
            text("SELECT value FROM system_settings WHERE key = 'developing_modules'")
        )
    assert developing == {"modules": ["FINANCE"]}
    assert [tuple(row) for row in grants] == [
        ("BOD", "HEAD", "CUSTOMERS"),
        ("BOD", "HEAD", "REVENUE"),
        ("KT", "HEAD", "CUSTOMERS"),
        ("KT", "HEAD", "REVENUE"),
        ("KT", "HEAD", "SETTINGS"),
        ("KT", "MEMBER", "CUSTOMERS"),
        ("KT", "MEMBER", "REVENUE"),
    ]
    await database.engine.dispose()
    await downgrade_to(dsn, "0104")
    async with database.engine.begin() as connection:
        table = await connection.scalar(text("SELECT to_regclass('crm_orders')"))
        column = await connection.scalar(
            text(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_name = 'ads_crm_payments' AND column_name = 'customer_name'"
            )
        )
        left = await connection.scalar(
            text("SELECT count(*) FROM department_permissions WHERE module = 'FINANCE'")
        )
    assert table is None and column == 0 and left == 0
    await database.engine.dispose()
    await upgrade_to(dsn, "0105")


async def test_02_fees_costs_kpi(dsn: str, database: Database) -> None:
    """0106: the fee / cost / KPI tables match the models; FEES seeded for KT."""
    await upgrade_to(dsn, "0106")

    def _diff(connection: object) -> list[object]:
        from alembic.autogenerate import compare_metadata
        from alembic.migration import MigrationContext

        context = MigrationContext.configure(
            connection,  # type: ignore[arg-type]
            opts={"compare_type": True, "compare_server_default": True},
        )
        return [
            item
            for item in compare_metadata(context, Base.metadata)
            if any(
                f"'{table}'" in repr(item)
                for table in ("finance_fees", "finance_service_costs", "finance_kpis")
            )
            and not _later(item)
        ]

    async with database.engine.begin() as connection:
        assert await connection.run_sync(_diff) == []
        fees = (
            await connection.execute(
                text(
                    "SELECT d.code, p.position FROM department_permissions p "
                    "JOIN departments d ON d.id = p.department_id "
                    "WHERE p.module = 'FINANCE' AND p.permission = 'FEES' ORDER BY 1, 2"
                )
            )
        ).all()
    assert [tuple(row) for row in fees] == [("KT", "HEAD"), ("KT", "MEMBER")]
    await database.engine.dispose()
    await downgrade_to(dsn, "0105")
    async with database.engine.begin() as connection:
        assert await connection.scalar(text("SELECT to_regclass('finance_fees')")) is None
    await database.engine.dispose()
    await upgrade_to(dsn, "0106")


async def test_03_spend_tables_codes_grants(dsn: str, database: Database) -> None:
    """0107: hồ sơ chi tables match the models, the Sổ tay's codes are in, the
    KT / BOD grants seeded; a downgrade removes them."""
    await upgrade_to(dsn, "0107")

    def _diff(connection: object) -> list[object]:
        from alembic.autogenerate import compare_metadata
        from alembic.migration import MigrationContext

        context = MigrationContext.configure(
            connection,  # type: ignore[arg-type]
            opts={"compare_type": True, "compare_server_default": True},
        )
        return [
            item
            for item in compare_metadata(context, Base.metadata)
            if "finance_" in repr(item) and not _later(item)
        ]

    async with database.engine.begin() as connection:
        assert await connection.run_sync(_diff) == []
        codes = await connection.scalar(text("SELECT count(*) FROM finance_codes"))
        bank = await connection.scalar(
            text("SELECT route FROM finance_codes WHERE code = 'CO.06.06.02'")
        )
        grants = (
            await connection.execute(
                text(
                    "SELECT d.code, p.position, count(*) FROM department_permissions p "
                    "JOIN departments d ON d.id = p.department_id WHERE p.module = 'FINANCE' "
                    "AND p.permission IN ('VIEW_REQUESTS','CODES','BUDGET','MISA','REPORT') "
                    "GROUP BY 1, 2 ORDER BY 1, 2"
                )
            )
        ).all()
    assert codes == 104 and bank["no_approval"] is True
    assert [tuple(row) for row in grants] == [
        ("BOD", "HEAD", 2),
        ("KT", "HEAD", 5),
        ("KT", "MEMBER", 2),
    ]
    await database.engine.dispose()
    await downgrade_to(dsn, "0106")
    async with database.engine.begin() as connection:
        assert await connection.scalar(text("SELECT to_regclass('finance_requests')")) is None
    await database.engine.dispose()
    await upgrade_to(dsn, "0107")


async def test_04_crm_staff_follow_phong_ads(dsn: str, database: Database) -> None:
    """0108: every Phòng Ads CRM account gives its link to ``crm_staff`` -
    a missing row is created (role marketing), a different link replaced."""
    await upgrade_to(dsn, "0107")
    marketer, other = uuid.uuid4(), uuid.uuid4()
    async with database.engine.begin() as connection:
        for user_id, name in ((marketer, "MKT A"), (other, "Người khác")):
            await connection.execute(
                text(
                    "INSERT INTO users (id, full_name, role, status, active, created_at, "
                    "updated_at) VALUES (:id, :name, 'EMPLOYEE', 'ACTIVE', true, now(), now())"
                ),
                {"id": user_id, "name": name},
            )
        await connection.execute(
            text(
                "INSERT INTO ads_crm_accounts (crm_id, name, active, in_directory, user_id, "
                "former, synced_at) VALUES (10, 'HN - MKT A', true, true, :u, false, now()), "
                "(11, 'Hotline FB', true, true, NULL, false, now())"
            ),
            {"u": marketer},
        )
        await connection.execute(
            text(
                "INSERT INTO crm_staff (id, name, roles, user_id, seen_at) VALUES "
                "(11, 'Hotline FB', '[\"telesales\"]', :o, now()), "
                "(20, 'Sale B', '[\"telesales\"]', :o, now())"
            ),
            {"o": other},
        )
    await database.engine.dispose()
    await upgrade_to(dsn, "0108")
    async with database.engine.begin() as connection:
        rows = {
            row.id: (row.user_id, sorted(row.roles))
            for row in (
                await connection.execute(text("SELECT id, user_id, roles FROM crm_staff"))
            ).all()
        }
    assert rows == {
        10: (marketer, ["marketing"]),
        11: (None, ["marketing", "telesales"]),
        20: (other, ["telesales"]),
    }
    await database.engine.dispose()


async def test_05_ledger_marks(dsn: str, database: Database) -> None:
    """0109: the five "BCLT new" marks on finance_fees match the model; a
    downgrade drops them."""
    await upgrade_to(dsn, "0109")

    def _diff(connection: object) -> list[object]:
        from alembic.autogenerate import compare_metadata
        from alembic.migration import MigrationContext

        context = MigrationContext.configure(
            connection,  # type: ignore[arg-type]
            opts={"compare_type": True, "compare_server_default": True},
        )
        return [
            item
            for item in compare_metadata(context, Base.metadata)
            if "'finance_fees'" in repr(item)
        ]

    async with database.engine.begin() as connection:
        assert await connection.run_sync(_diff) == []
    await database.engine.dispose()
    await downgrade_to(dsn, "0108")
    async with database.engine.begin() as connection:
        names = (
            await connection.scalars(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'finance_fees'"
                )
            )
        ).all()
    assert not set(LATER_FEE_COLUMNS) & set(names)
    await database.engine.dispose()
    await upgrade_to(dsn, "0109")
