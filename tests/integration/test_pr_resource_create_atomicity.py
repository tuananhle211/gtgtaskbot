"""Step 1F.2.3e.1 on a real PostgreSQL: create-with-resources is one transaction.

The offline suite proves the rules - which locations are refused, which draft the
refusal names, that a savepoint rollback undoes the rows. It cannot prove the
claim this step is actually built on, because SQLite in the unit fixture never
commits: **a create that fails leaves nothing on disk.** Not the content item,
not the resources that were valid, not the audit rows, and not a half-written
aggregate somebody finds a week later and cannot explain.

Three scenarios, and the first is the one the requirement names:

* two valid resources and an invalid third - the shape a person actually
  produces, having pasted three links and got one of them wrong;
* a failure that happens **after** the content row, its version and its
  resources are already in the database - an unknown channel, which is refused
  once everything else has been flushed. This is the scenario a pre-validation
  check alone would not cover, and the one that would survive a client-side
  "create, then post each resource";
* a valid create, so none of the above is passing merely because nothing is ever
  written.

Run it against a PostgreSQL you are willing to have scratch databases created in
and dropped from::

    export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/postgres
    uv run pytest tests/integration/test_pr_resource_create_atomicity.py -m integration

The fixture creates its own uniquely-named ``meobot_res1_*`` database and drops
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

from meobot.application.pr_content_resource_support import ContentResourceSpec
from meobot.application.pr_content_service import ContentTargetSpec, CreateContentCommand
from meobot.application.pr_services import build_pr_services
from meobot.core.config import Settings, get_settings
from meobot.db.models.pr import PrBrand
from meobot.db.models.user import User
from meobot.db.session import Database
from meobot.domain.identity.models import Actor, Role
from meobot.domain.pr.errors import PrNotFoundError, PrValidationError
from meobot.domain.pr.models import PrContentResourceType, PrContentType
from tests.integration.test_dispatch_migrations import TEST_DATABASE_URL, upgrade_to

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="module"),
    pytest.mark.skipif(
        not TEST_DATABASE_URL,
        reason="Set MEOBOT_TEST_DATABASE_URL to run integration tests",
    ),
]

DRIVE = "https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUv/view"

BRIEF = ContentResourceSpec(
    resource_type=PrContentResourceType.REFERENCE,
    label="Brief khách hàng",
    location=DRIVE,
)
PACKSHOT = ContentResourceSpec(
    resource_type=PrContentResourceType.IMAGE,
    label="Ảnh packshot sản phẩm",
    location="/volume1/PR/2026/packshot.jpg",
    note="Dùng packshot số 3",
    required_for_review=True,
)
POISONED = ContentResourceSpec(
    resource_type=PrContentResourceType.VIDEO,
    label="Video tham khảo",
    location="javascript:alert(1)",
)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def pr_database() -> AsyncIterator[Database]:
    assert TEST_DATABASE_URL is not None
    base = make_url(TEST_DATABASE_URL)
    scratch = f"meobot_res1_{uuid.uuid4().hex[:12]}"

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


@pytest_asyncio.fixture(loop_scope="module")
async def author(pr_database: Database) -> tuple[Actor, uuid.UUID, uuid.UUID]:
    """Somebody who may create content, and a brand to create it against."""
    async with pr_database.transaction() as session:
        owner = User(full_name="Nguyễn A", role=Role.OWNER)
        brand = PrBrand(code=f"BRND-{uuid.uuid4().hex[:8]}", name="Apexmed")
        session.add_all([owner, brand])
        await session.flush()
        owner_id, brand_id = owner.id, brand.id
    return Actor(user_id=owner_id, full_name="Nguyễn A", role=Role.OWNER), owner_id, brand_id


async def _counts(database: Database) -> dict[str, int]:
    """Everything a create writes, counted from a session of its own.

    A fresh session on purpose: reading through the one that did the work would
    see its own uncommitted state, which is precisely the thing under test.
    """
    async with database.session() as session:
        rows = await session.execute(
            text(
                "SELECT "
                "(SELECT count(*) FROM pr_content_items) AS items, "
                "(SELECT count(*) FROM pr_content_versions) AS versions, "
                "(SELECT count(*) FROM pr_content_resources) AS resources, "
                "(SELECT count(*) FROM audit_logs "
                " WHERE action = 'pr.content.resource_added') AS resource_events, "
                "(SELECT count(*) FROM audit_logs "
                " WHERE action = 'pr.content.created') AS content_events"
            )
        )
        return dict(rows.mappings().one())


async def _create(
    database: Database,
    author: tuple[Actor, uuid.UUID, uuid.UUID],
    *,
    title: str,
    resources: tuple[ContentResourceSpec, ...],
    targets: tuple[ContentTargetSpec, ...] = (),
) -> uuid.UUID:
    actor, owner_id, brand_id = author
    async with database.transaction() as session:
        services = build_pr_services(session, get_settings())
        snapshot = await services.content.create_content(
            actor=actor,
            request_id=uuid.uuid4(),
            command=CreateContentCommand(
                title=title,
                brand_id=brand_id,
                owner_user_id=owner_id,
                content_type=PrContentType.SHORT_VIDEO_SCRIPT,
                targets=targets,
                initial_resources=resources,
            ),
        )
        return snapshot.content.id


async def test_a_valid_create_commits_the_item_and_all_of_its_resources(
    pr_database: Database, author: tuple[Actor, uuid.UUID, uuid.UUID]
) -> None:
    """The control. Without it, "nothing was written" proves nothing."""
    before = await _counts(pr_database)
    content_id = await _create(
        pr_database, author, title="Bài có tài nguyên", resources=(BRIEF, PACKSHOT)
    )
    after = await _counts(pr_database)

    assert after["items"] == before["items"] + 1
    assert after["resources"] == before["resources"] + 2
    assert after["resource_events"] == before["resource_events"] + 2
    assert after["content_events"] == before["content_events"] + 1

    async with pr_database.session() as session:
        stored = (
            await session.execute(
                text(
                    "SELECT label, required_for_review, note FROM pr_content_resources "
                    "WHERE content_id = :id ORDER BY required_for_review DESC, label"
                ),
                {"id": content_id},
            )
        ).all()
    assert [row[0] for row in stored] == ["Ảnh packshot sản phẩm", "Brief khách hàng"]
    assert stored[0][1] is True
    assert stored[0][2] == "Dùng packshot số 3"


async def test_an_invalid_third_resource_leaves_the_database_untouched(
    pr_database: Database, author: tuple[Actor, uuid.UUID, uuid.UUID]
) -> None:
    """The requirement, on a database that really commits.

    Two good pastes and one bad one. What must not exist afterwards is a content
    item holding two references, which is exactly what a create-then-post-each
    client produces and what nobody would notice until a reviewer opened the
    piece and found the brief missing.
    """
    before = await _counts(pr_database)

    with pytest.raises(PrValidationError) as refused:
        await _create(
            pr_database,
            author,
            title="Bài không được tạo",
            resources=(BRIEF, PACKSHOT, POISONED),
        )
    assert refused.value.details["initial_resource_index"] == 2
    assert refused.value.details["reason"] == "unsafe_scheme"

    assert await _counts(pr_database) == before

    async with pr_database.session() as session:
        found = (
            await session.execute(
                text("SELECT count(*) FROM pr_content_items WHERE title = :title"),
                {"title": "Bài không được tạo"},
            )
        ).scalar_one()
    assert found == 0


async def test_a_failure_after_the_rows_exist_takes_them_with_it(
    pr_database: Database, author: tuple[Actor, uuid.UUID, uuid.UUID]
) -> None:
    """The case a pre-flight check cannot cover.

    The resources here are valid, so validation passes and the content row, its
    version and both resource rows are flushed. The refusal comes afterwards,
    from a channel that does not exist. Everything already written has to go with
    it - including the content code, which is a counter row rather than a
    sequence precisely so that it does.
    """
    before = await _counts(pr_database)

    with pytest.raises(PrNotFoundError):
        await _create(
            pr_database,
            author,
            title="Bài kênh lạ",
            resources=(BRIEF, PACKSHOT),
            targets=(ContentTargetSpec(channel_id=uuid.uuid4()),),
        )

    assert await _counts(pr_database) == before
