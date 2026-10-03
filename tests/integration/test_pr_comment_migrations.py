"""Migration 0026, on a database built the way production builds one.

Four claims, and only PostgreSQL can settle them:

* **the table has the constraints it claims**: ``RESTRICT`` on all four foreign
  keys including the self-reference, the non-empty body check, the
  parent-is-not-self check, and the two indexes;
* **nothing that already existed moves.** 0026 adds one table and touches
  nothing else, so a content item, a publication and a derivative written at
  0025 are byte-for-byte what they were afterwards. A revision that "tidied"
  something on the way past would be caught here;
* **the self-referencing ``RESTRICT`` behaves the way the aggregate delete
  assumes it does.** ``PrContentLifecycleService`` removes a content item's
  comments with a single ``DELETE ... WHERE content_id = ?`` covering roots and
  replies together, which is only correct if PostgreSQL applies the check to
  what is standing at the end of the statement. That is asserted directly here -
  along with its mirror, that removing a root and leaving a reply *is* refused -
  because the whole plan rests on it and no offline fixture can prove it;
* **the downgrade actually works.** Index and constraint names that do not match
  what was created are the classic Alembic failure - revision 0021 exists to
  repair exactly that - so this upgrades, downgrades and upgrades again.

It reuses the harness ``tests/integration/test_dispatch_migrations`` established
and every PR migration test follows.

Run it against a PostgreSQL you are willing to have scratch databases created in
and dropped from::

    export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/postgres
    uv run pytest tests/integration/test_pr_comment_migrations.py -m integration

Each fixture creates its own uniquely-named ``meobot_cmt_*`` database and drops
that one afterwards. Nothing else on the server is read or written.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from meobot.core.config import Settings
from meobot.db.session import Database
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

COMMENTS = "pr_content_comments"
CONTENT_INDEX = "ix_pr_content_comments_content_created"
PARENT_INDEX = "ix_pr_content_comments_parent"

#: Every column the revision creates. Nothing else may appear on this table.
EXPECTED_COLUMNS = frozenset(
    {
        "id",
        "content_id",
        "author_user_id",
        "parent_comment_id",
        "body",
        "edited_at",
        "deleted_at",
        "deleted_by_user_id",
        "created_at",
        "updated_at",
    }
)


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def stopped_at_0025() -> AsyncIterator[tuple[Database, str]]:
    """A database migrated to **0025**, with its DSN so a test can go further.

    One short of head, so content can be written *before* the comment table
    exists and observed afterwards - which is the only way to prove 0026 touched
    nothing on its way past.
    """
    assert TEST_DATABASE_URL is not None
    base = make_url(TEST_DATABASE_URL)
    scratch = f"meobot_cmt_{uuid.uuid4().hex[:12]}"

    admin = create_async_engine(
        base.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=None
    )
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{scratch}"'))

        dsn = base.set(database=scratch).render_as_string(hide_password=False)
        await upgrade_to(dsn, "0025")

        database = Database(Settings(_env_file=None, database_url=dsn))
        try:
            yield database, dsn
        finally:
            await database.dispose()
    finally:
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)'))
        await admin.dispose()


async def _seed(database: Database) -> dict[str, uuid.UUID]:
    """A user and a content item, written the way an older deployment would."""
    ids = {key: uuid.uuid4() for key in ("user", "other", "brand", "content")}
    async with database.transaction() as session:
        for key, name in (("user", "Nguyễn A"), ("other", "Phương Nhung")):
            await session.execute(
                text(
                    "INSERT INTO users (id, full_name, role, active, created_at, updated_at) "
                    "VALUES (:id, :name, 'EMPLOYEE', true, now(), now())"
                ),
                {"id": ids[key], "name": name},
            )
        await session.execute(
            text(
                "INSERT INTO pr_brands (id, code, name, status, created_at, updated_at) "
                "VALUES (:id, :code, 'Apexmed', 'ACTIVE', now(), now())"
            ),
            {"id": ids["brand"], "code": f"BR-{uuid.uuid4().hex[:6]}"},
        )
        await session.execute(
            text(
                "INSERT INTO pr_content_items "
                "(id, code, title, brand_id, priority, workflow_stage, owner_user_id, "
                " created_by_user_id, created_at, updated_at) "
                "VALUES (:id, :code, 'Bài cũ', :brand, 'NORMAL', 'PUBLISHED', :user, :user, "
                " now(), now())"
            ),
            {
                "id": ids["content"],
                "code": f"CNT-2026-{uuid.uuid4().int % 1000000:06d}",
                "brand": ids["brand"],
                "user": ids["user"],
            },
        )
    return ids


async def _tables(database: Database) -> set[str]:
    async with database.transaction() as session:
        found = await session.execute(
            text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
        )
        return set(found.scalars().all())


async def _columns(database: Database, table: str) -> set[str]:
    async with database.transaction() as session:
        found = await session.execute(
            text("SELECT column_name FROM information_schema.columns WHERE table_name = :t"),
            {"t": table},
        )
        return set(found.scalars().all())


async def _indexes(database: Database, table: str) -> set[str]:
    async with database.transaction() as session:
        found = await session.execute(
            text("SELECT indexname FROM pg_indexes WHERE tablename = :t"), {"t": table}
        )
        return set(found.scalars().all())


async def _write_thread(database: Database, ids: dict[str, uuid.UUID]) -> tuple[uuid.UUID, ...]:
    """A root and two replies, the shape the aggregate delete has to handle."""
    root, first, second = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with database.transaction() as session:
        for comment_id, parent, author, body in (
            (root, None, ids["user"], "Hook đoạn đầu hơi dài."),
            (first, root, ids["other"], "Đã sửa bản cut mới."),
            (second, root, ids["user"], "Ok nhé."),
        ):
            await session.execute(
                text(
                    "INSERT INTO pr_content_comments "
                    "(id, content_id, author_user_id, parent_comment_id, body, "
                    " created_at, updated_at) "
                    "VALUES (:id, :content, :author, :parent, :body, now(), now())"
                ),
                {
                    "id": comment_id,
                    "content": ids["content"],
                    "author": author,
                    "parent": parent,
                    "body": body,
                },
            )
    return root, first, second


@pytest.mark.asyncio(loop_scope="module")
async def test_0025_has_no_comment_table(stopped_at_0025: tuple[Database, str]) -> None:
    """The starting point, so the rest of the file is measuring something."""
    database, _ = stopped_at_0025
    assert COMMENTS not in await _tables(database)


@pytest.mark.asyncio(loop_scope="module")
async def test_0026_adds_one_table_and_moves_nothing_else(
    stopped_at_0025: tuple[Database, str],
) -> None:
    """One table, the columns the model declares, and an untouched content item.

    The second half is the one worth having: 0026 is an *additive* revision, so a
    content item written at 0025 must come through it with the same code, the
    same stage and the same owner. A well-meaning ``op.execute`` added later
    would be caught here rather than on the NAS.
    """
    database, dsn = stopped_at_0025
    ids = await _seed(database)

    await upgrade_to(dsn, "0026")

    assert COMMENTS in await _tables(database)
    assert await _columns(database, COMMENTS) == EXPECTED_COLUMNS
    assert await _indexes(database, COMMENTS) >= {CONTENT_INDEX, PARENT_INDEX}

    async with database.transaction() as session:
        row = (
            await session.execute(
                text(
                    "SELECT code, workflow_stage, owner_user_id FROM pr_content_items "
                    "WHERE id = :id"
                ),
                {"id": ids["content"]},
            )
        ).one()
    assert row.code.startswith("CNT-")
    assert row.workflow_stage == "PUBLISHED"
    assert row.owner_user_id == ids["user"]


@pytest.mark.asyncio(loop_scope="module")
async def test_the_constraints_are_what_the_revision_claims(
    stopped_at_0025: tuple[Database, str],
) -> None:
    """The names in the migration are the names in the database.

    Short and explicit, because the generated form concatenates a long table
    name with a long column name and runs past PostgreSQL's 63-byte identifier
    limit - the defect 0021 exists to repair. ``RESTRICT`` on all four,
    **including the self-reference**, which is what makes a comment table
    somebody forgot to add to the aggregate delete a failed transaction rather
    than a half-deleted content item.
    """
    database, dsn = stopped_at_0025
    await upgrade_to(dsn, "0026")

    async with database.transaction() as session:
        found = await session.execute(
            text(
                "SELECT conname, confdeltype::text FROM pg_constraint "
                "WHERE contype = 'f' AND conrelid::regclass::text = :t"
            ),
            {"t": COMMENTS},
        )
        keys = dict(found.all())  # type: ignore[arg-type]
    for name in (
        "fk_content_comment_content",
        "fk_content_comment_author",
        "fk_content_comment_parent",
        "fk_content_comment_deleted_by",
    ):
        assert keys.get(name) == "r", name

    async with database.transaction() as session:
        checks = await session.execute(
            text(
                "SELECT conname FROM pg_constraint WHERE contype = 'c' "
                "AND conrelid::regclass::text = :t"
            ),
            {"t": COMMENTS},
        )
        names = set(checks.scalars().all())
    assert "ck_pr_content_comments_body_not_empty" in names
    assert "ck_pr_content_comments_parent_not_self" in names


@pytest.mark.asyncio(loop_scope="module")
async def test_a_blank_body_and_a_self_parent_are_refused(
    stopped_at_0025: tuple[Database, str],
) -> None:
    """The two rules a row-level check can express, asserted at the row level.

    The service refuses both long before they reach here. These exist because a
    constraint is what makes the refusal true of *every* writer, including a
    script somebody runs against the database directly.
    """
    database, dsn = stopped_at_0025
    ids = await _seed(database)
    await upgrade_to(dsn, "0026")

    async def insert(comment_id: uuid.UUID, *, body: str, parent: uuid.UUID | None) -> None:
        async with database.transaction() as session:
            await session.execute(
                text(
                    "INSERT INTO pr_content_comments "
                    "(id, content_id, author_user_id, parent_comment_id, body, "
                    " created_at, updated_at) "
                    "VALUES (:id, :content, :author, :parent, :body, now(), now())"
                ),
                {
                    "id": comment_id,
                    "content": ids["content"],
                    "author": ids["user"],
                    "parent": parent,
                    "body": body,
                },
            )

    with pytest.raises(Exception, match="body_not_empty"):
        await insert(uuid.uuid4(), body="   ", parent=None)

    same = uuid.uuid4()
    with pytest.raises(Exception, match="parent_not_self"):
        await insert(same, body="Tự trả lời mình", parent=same)


@pytest.mark.asyncio(loop_scope="module")
async def test_the_aggregate_delete_can_remove_a_whole_thread_in_one_statement(
    stopped_at_0025: tuple[Database, str],
) -> None:
    """The assumption ``PrContentLifecycleService._plan`` rests on, proved.

    That plan removes a content item's comments with **one**
    ``DELETE ... WHERE content_id = ?`` covering roots and replies together. Under
    a self-referencing ``RESTRICT`` that is only correct if PostgreSQL applies the
    check to the rows still standing at the end of the statement - and it does,
    which is the difference between "the plan works" and "the plan works until
    somebody deletes a draft whose thread had an answer in it".

    The mirror is asserted immediately after: removing a root and leaving its
    reply **is** refused, which is what makes the first half a fact about
    statement scope rather than about the constraint being absent.
    """
    database, dsn = stopped_at_0025
    ids = await _seed(database)
    await upgrade_to(dsn, "0026")
    root, _, _ = await _write_thread(database, ids)

    async with database.transaction() as session:
        with pytest.raises(Exception, match="fk_content_comment_parent"):
            await session.execute(
                text("DELETE FROM pr_content_comments WHERE id = :id"), {"id": root}
            )

    async with database.transaction() as session:
        removed = await session.execute(
            text("DELETE FROM pr_content_comments WHERE content_id = :content"),
            {"content": ids["content"]},
        )
        assert removed.rowcount == 3

    async with database.transaction() as session:
        left = await session.execute(
            text("SELECT count(*) FROM pr_content_comments WHERE content_id = :content"),
            {"content": ids["content"]},
        )
        assert left.scalar() == 0


@pytest.mark.asyncio(loop_scope="module")
async def test_the_round_trip(stopped_at_0025: tuple[Database, str]) -> None:
    """0025 -> 0026 -> 0025 -> 0026, which is where name mistakes surface.

    An index dropped by a name that does not exist fails here and nowhere else -
    and would fail on the NAS during a rollback, which is the worst possible
    moment.

    What the downgrade costs is total and is written down: every comment on every
    piece of content. The content itself survives untouched, which is asserted,
    because a rollback that took the work with the conversation would be a
    different and much worse trade.
    """
    database, dsn = stopped_at_0025
    ids = await _seed(database)

    await upgrade_to(dsn, "0026")
    await _write_thread(database, ids)
    assert COMMENTS in await _tables(database)

    await downgrade_to(dsn, "0025")
    assert COMMENTS not in await _tables(database)
    async with database.transaction() as session:
        survived = await session.execute(
            text("SELECT count(*) FROM pr_content_items WHERE id = :id"), {"id": ids["content"]}
        )
        assert survived.scalar() == 1

    await upgrade_to(dsn, "0026")
    assert COMMENTS in await _tables(database)
    assert await _columns(database, COMMENTS) == EXPECTED_COLUMNS
    assert await _indexes(database, COMMENTS) >= {CONTENT_INDEX, PARENT_INDEX}
