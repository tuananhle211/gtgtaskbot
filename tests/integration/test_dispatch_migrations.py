"""The dispatch tables, on a database built the way production builds one.

This file exists because of a defect the entire offline suite could not see.

``TimestampMixin`` declares ``created_at``/``updated_at`` with a **server**
default and no Python default, so the ORM omits both from its ``INSERT`` and
expects PostgreSQL to fill them. Migration ``0010`` created the six dispatch
tables ``NOT NULL`` with no default. In production the first draft insert
failed::

    null value in column "created_at" of relation "message_dispatch_drafts"
    violates not-null constraint

Every offline test passed throughout, and would have kept passing forever: they
build their schema with ``Base.metadata.create_all()``, which reads the
*models* - and the models were right. The one place the model and the schema
disagreed was the one place nothing looked.

So this test does not use ``create_all`` at all. It creates an empty database,
runs the **real Alembic chain** through head, and inserts the whole dispatch
chain the way the application does: without mentioning a timestamp anywhere.

Run it against a PostgreSQL you are willing to have scratch databases created
in and dropped from::

    export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/postgres
    uv run pytest tests/integration/test_dispatch_migrations.py -m integration

Each test creates its own uniquely-named ``meobot_mig_*`` database and drops
that one afterwards. Nothing else on the server is read or written.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from meobot.core.config import Settings, get_settings
from meobot.db.models.dispatch import (
    MessageDispatch,
    MessageDispatchDraft,
    MessageDispatchDraftRecipient,
    MessageDispatchPart,
    MessageDispatchRecipient,
    MessageDispatchRecipientPart,
)
from meobot.db.models.notifications import TelegramChat
from meobot.db.session import Database
from meobot.domain.dispatch.models import (
    DispatchPartStatus,
    DispatchRecipientStatus,
    DispatchStatus,
    DraftStatus,
    SelectionSource,
)
from meobot.domain.notifications.models import ChatPurpose, PrivacyClassification

ROOT = Path(__file__).resolve().parents[2]
TEST_DATABASE_URL = os.environ.get("MEOBOT_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not TEST_DATABASE_URL,
        reason="Set MEOBOT_TEST_DATABASE_URL to run integration tests",
    ),
]

BOT_ID = 424242

#: Exactly the columns migration ``0011`` is responsible for. Written out here
#: rather than derived from the models, so that deleting the default from a
#: model does not quietly delete the assertion that it exists.
TIMESTAMP_COLUMNS: tuple[tuple[str, str], ...] = (
    ("message_dispatch_drafts", "created_at"),
    ("message_dispatch_drafts", "updated_at"),
    ("message_dispatch_draft_recipients", "created_at"),
    ("message_dispatch_draft_recipients", "updated_at"),
    ("message_dispatches", "created_at"),
    ("message_dispatches", "updated_at"),
    ("message_dispatch_recipients", "created_at"),
    ("message_dispatch_recipients", "updated_at"),
    ("message_dispatch_parts", "created_at"),
    ("message_dispatch_recipient_parts", "created_at"),
    ("message_dispatch_recipient_parts", "updated_at"),
)


def _alembic(url: str, action: str, revision: str) -> None:
    """Run one Alembic command against ``url``, synchronously.

    Called through :func:`asyncio.to_thread`, and that is not incidental:
    ``alembic/env.py`` calls :func:`asyncio.run`, which refuses to nest inside
    the loop pytest-asyncio is already running. A worker thread has no loop of
    its own, so ``env.py`` runs exactly as it does on the command line.

    ``env.py`` reads the DSN from :func:`get_settings`, never from
    ``alembic.ini``, so the environment is what has to be pointed at the
    scratch database - and put back afterwards, because the rest of the suite
    is still using the settings this process was started with.
    """
    from alembic import command
    from alembic.config import Config

    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    get_settings.cache_clear()
    try:
        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(ROOT / "alembic"))
        getattr(command, action)(config, revision)
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
        get_settings.cache_clear()


async def upgrade_to(url: str, revision: str = "head") -> None:
    await asyncio.to_thread(_alembic, url, "upgrade", revision)


async def downgrade_to(url: str, revision: str) -> None:
    await asyncio.to_thread(_alembic, url, "downgrade", revision)


def alembic_head() -> str:
    """The newest revision on disk, read from the filenames.

    Shared by the suites that migrate to ``head`` and then assert what they
    landed on. Each of them used to write the number in - ``"0019"``,
    ``{"0016", "0017"}`` - which turned "this revision applies" into "this
    revision is the newest", and quietly went red the next time somebody added
    one. The revisions are zero-padded and sequential, so ``max`` is the head.
    """
    return max(
        path.stem.split("_")[0]
        for path in (ROOT / "alembic" / "versions").glob("*.py")
        if path.stem[0].isdigit()
    )


@pytest.fixture
async def migrated_database() -> AsyncIterator[Database]:
    """A brand-new database with the full migration chain applied.

    Deliberately **not** ``Base.metadata.create_all()``. Building the schema
    from the models is what hid the original defect: it can only ever agree
    with the models, so it can never catch a migration that does not.
    """
    assert TEST_DATABASE_URL is not None
    base = make_url(TEST_DATABASE_URL)
    scratch = f"meobot_mig_{uuid.uuid4().hex[:12]}"

    # A maintenance connection: CREATE/DROP DATABASE cannot run inside a
    # transaction, hence AUTOCOMMIT.
    admin = create_async_engine(
        base.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=None
    )
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{scratch}"'))

        url = base.set(database=scratch)
        dsn = url.render_as_string(hide_password=False)
        await upgrade_to(dsn)

        database = Database(Settings(_env_file=None, database_url=dsn))
        try:
            yield database
        finally:
            await database.dispose()
    finally:
        async with admin.connect() as connection:
            # FORCE closes any connection this test left behind. Only ever
            # applied to the scratch database this fixture just created.
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)'))
        await admin.dispose()


async def test_the_migration_chain_reaches_head(migrated_database: Database) -> None:
    """A blank database, migrated by Alembic, ends at the current head."""
    async with migrated_database.session() as session:
        result = await session.execute(text("SELECT version_num FROM alembic_version"))
        stamped = result.scalar_one()
    assert stamped == alembic_head()


async def test_every_dispatch_timestamp_column_has_a_server_default(
    migrated_database: Database,
) -> None:
    """The assertion the production error would have failed on.

    Reads the live catalog rather than the migration source: what matters is
    what the database ended up with, not what a file says it should have.
    """
    async with migrated_database.session() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT table_name, column_name, column_default, is_nullable "
                    "FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name LIKE 'message_dispatch%'"
                )
            )
        ).all()

    defaults = {(row[0], row[1]): (row[2], row[3]) for row in rows}
    missing = []
    for table, column in TIMESTAMP_COLUMNS:
        found = defaults.get((table, column))
        assert found is not None, f"{table}.{column} is not in the migrated schema"
        default, nullable = found
        assert nullable == "NO", f"{table}.{column} should stay NOT NULL"
        if not default or "now()" not in default:
            missing.append(f"{table}.{column} (default={default!r})")
    assert missing == [], "columns without a server default: " + ", ".join(missing)


async def test_the_whole_dispatch_chain_inserts_without_any_timestamp(
    migrated_database: Database,
) -> None:
    """Insert every dispatch row the way the application does: no timestamps.

    This is the exact shape of the production failure. Before ``0011`` the very
    first flush raised ``IntegrityError`` on ``message_dispatch_drafts``.
    """
    async with migrated_database.transaction() as session:
        chat = TelegramChat(
            telegram_chat_id=-100_500,
            bot_identity=BOT_ID,
            chat_type="supergroup",
            telegram_title="Group Test",
            display_name="Group Test",
            normalized_alias="group test",
            purpose=ChatPurpose.GENERAL,
            privacy_level=PrivacyClassification.PUBLIC_OPERATIONAL,
            registered_at=datetime.now(UTC),
        )
        session.add(chat)
        await session.flush()

        draft = MessageDispatchDraft(
            bot_identity=BOT_ID,
            created_by_telegram_id=777_000_111,
            source_chat_id=777_000_111,
            original_text="Gửi cho tất cả group: Buổi tối vui vẻ.",
            rendered_text="Buổi tối vui vẻ.",
            status=DraftStatus.CHOOSING,
            expires_at=datetime.now(UTC),
        )
        session.add(draft)
        await session.flush()

        draft_recipient = MessageDispatchDraftRecipient(
            draft_id=draft.id,
            recipient_chat_row_id=chat.id,
            position=1,
            display_name="Group Test",
            selected=True,
            selection_source=SelectionSource.NAMED,
        )
        session.add(draft_recipient)

        dispatch = MessageDispatch(
            bot_identity=BOT_ID,
            created_by_telegram_id=777_000_111,
            source_chat_id=777_000_111,
            content="Buổi tối vui vẻ.",
            status=DispatchStatus.QUEUED,
            recipient_count=1,
            total_parts=1,
        )
        session.add(dispatch)
        await session.flush()

        part = MessageDispatchPart(
            dispatch_id=dispatch.id, part_number=1, total_parts=1, content="Buổi tối vui vẻ."
        )
        session.add(part)

        recipient = MessageDispatchRecipient(
            dispatch_id=dispatch.id,
            telegram_chat_row_id=chat.id,
            telegram_chat_id=chat.telegram_chat_id,
            destination_display_name="Group Test",
            position=1,
            status=DispatchRecipientStatus.QUEUED,
        )
        session.add(recipient)
        await session.flush()

        session.add(
            MessageDispatchRecipientPart(
                recipient_id=recipient.id,
                dispatch_part_id=part.id,
                part_number=1,
                status=DispatchPartStatus.QUEUED,
            )
        )
        draft_id, dispatch_id = draft.id, dispatch.id

    # Read every row back and check the database filled in what the ORM omitted.
    async with migrated_database.session() as session:
        for table, column in TIMESTAMP_COLUMNS:
            nulls = (
                await session.execute(
                    text(f"SELECT count(*) FROM {table} WHERE {column} IS NULL")  # noqa: S608
                )
            ).scalar_one()
            assert nulls == 0, f"{table}.{column} came back null"

        counts = {
            name: (
                await session.execute(text(f"SELECT count(*) FROM {name}"))  # noqa: S608
            ).scalar_one()
            for name, _ in TIMESTAMP_COLUMNS
        }

    assert counts["message_dispatch_drafts"] == 1
    assert counts["message_dispatch_draft_recipients"] == 1
    assert counts["message_dispatches"] == 1
    assert counts["message_dispatch_parts"] == 1
    assert counts["message_dispatch_recipients"] == 1
    assert counts["message_dispatch_recipient_parts"] == 1
    assert draft_id is not None and dispatch_id is not None


async def test_updated_at_is_maintained_by_the_database_on_change(
    migrated_database: Database,
) -> None:
    """``updated_at`` is filled on insert and moved on update.

    Worth its own check because the two come from different places - the
    insert default is the server's, the update clock is ``onupdate`` - and only
    the first is what ``0011`` restores. Read back through a fresh session
    rather than off the instance: a server-side default expires the attribute,
    and touching an expired attribute in async code is IO in the wrong place.
    """
    async with migrated_database.transaction() as session:
        draft = MessageDispatchDraft(
            bot_identity=BOT_ID,
            created_by_telegram_id=1,
            source_chat_id=1,
            original_text="x",
            rendered_text="x",
            status=DraftStatus.CHOOSING,
            expires_at=datetime.now(UTC),
        )
        session.add(draft)
        await session.flush()
        draft_id = draft.id

    async with migrated_database.session() as session:
        row = await session.execute(
            text("SELECT created_at, updated_at FROM message_dispatch_drafts WHERE id = :id"),
            {"id": draft_id},
        )
        created, first_update = row.one()
    assert created is not None
    assert first_update is not None

    async with migrated_database.transaction() as session:
        stored = await session.get(MessageDispatchDraft, draft_id)
        assert stored is not None
        stored.version += 1

    async with migrated_database.session() as session:
        row = await session.execute(
            text("SELECT updated_at, version FROM message_dispatch_drafts WHERE id = :id"),
            {"id": draft_id},
        )
        second_update, version = row.one()
    assert version == 2
    assert second_update is not None
    assert second_update >= first_update


async def test_without_0011_the_production_insert_fails(migrated_database: Database) -> None:
    """Step back one revision and watch the reported error come back.

    This is the test that makes the diagnosis falsifiable rather than merely
    plausible. On the ``0010`` schema the draft insert raises exactly what
    production raised; on ``0011`` the same insert succeeds. Nothing else about
    the two schemas differs.
    """
    from sqlalchemy.exc import IntegrityError

    # The fixture's own scratch DSN. Reaching into the Database for it keeps
    # the fixture's contract to one object, and this is the only test that
    # needs to migrate the schema out from under itself.
    dsn = str(migrated_database._settings.database_url)

    def _draft() -> MessageDispatchDraft:
        return MessageDispatchDraft(
            bot_identity=BOT_ID,
            created_by_telegram_id=1,
            source_chat_id=1,
            original_text="Gửi cho tất cả group: Buổi tối vui vẻ.",
            rendered_text="Buổi tối vui vẻ.",
            status=DraftStatus.CHOOSING,
            expires_at=datetime.now(UTC),
        )

    await downgrade_to(dsn, "0010")
    with pytest.raises(IntegrityError) as failure:
        async with migrated_database.transaction() as session:
            session.add(_draft())
            await session.flush()
    assert "created_at" in str(failure.value)

    await upgrade_to(dsn, "head")
    async with migrated_database.transaction() as session:
        session.add(_draft())
        await session.flush()
