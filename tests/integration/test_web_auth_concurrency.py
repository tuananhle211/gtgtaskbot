"""Two requests, one login link. Against real PostgreSQL.

Skipped unless ``MEOBOT_TEST_DATABASE_URL`` points at a database you are willing
to have rebuilt::

    docker run --rm -d --name pg-check \\
      -e POSTGRES_USER=meobot -e POSTGRES_PASSWORD=t -e POSTGRES_DB=meobot_test \\
      -p 127.0.0.1:55440:5432 postgres:16-alpine
    MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:t@127.0.0.1:55440/meobot_test \\
      uv run pytest tests/integration/test_web_auth_concurrency.py -q

Why this cannot live in the unit tests
--------------------------------------

Single-use is enforced by ``SELECT … FOR UPDATE`` on the login-token row. SQLite
has no row locks, so the offline suite runs the unlocked path and would pass
whether or not the lock exists - which is the worst kind of green test.

What the race actually is
-------------------------

Without the lock, two transactions both read the row while ``redeemed_at`` is
still null, both pass the "is it live" check, and **both insert a session**. One
link becomes two credentials.

That is not theoretical. A double-click on a link in the Telegram desktop client
does it, and so does a link-preview fetcher followed a moment later by the human.
The `expires_at > created_at` check constraint does not help, and neither does the
unique index on ``token_hash`` - both sessions get *different* tokens.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from meobot.application.web_auth_service import WebAuthService, hash_token
from meobot.core.config import Settings
from meobot.core.errors import ValidationError
from meobot.db.models.user import User
from meobot.db.models.web_session import WebSession, WebSessionKind
from meobot.db.session import Database
from meobot.domain.identity.models import Role

# Reused rather than reimplemented. ``alembic/env.py`` reads the DSN from
# ``get_settings()`` and never from ``alembic.ini``, so pointing a ``Config`` at a
# URL does nothing - the environment has to be swapped and put back. That is fiddly
# enough that a second copy gets it wrong, and the first version of this file did:
# it set ``sqlalchemy.url``, and every test failed trying to reach the DSN in
# ``tests/conftest.py``'s pinned environment.
from tests.integration.test_dispatch_migrations import upgrade_to as _alembic_upgrade

TEST_DATABASE_URL = os.environ.get("MEOBOT_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="Set MEOBOT_TEST_DATABASE_URL to run concurrency checks."
)

#: Concurrent redemption attempts. More than two, because a race that only two
#: transactions can lose is one an unlucky third can still win.
ATTEMPTS = 8


@pytest_asyncio.fixture
async def scratch() -> AsyncIterator[tuple[Database, Settings, uuid.UUID]]:
    """A throwaway database with one active user in it."""
    base = make_url(str(TEST_DATABASE_URL))
    name = f"meobot_authrace_{uuid.uuid4().hex[:12]}"

    admin = create_async_engine(
        base.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=None
    )
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{name}"'))
        dsn = base.set(database=name).render_as_string(hide_password=False)
        await _alembic_upgrade(dsn)

        settings = Settings(
            _env_file=None,  # type: ignore[call-arg]
            database_url=dsn,
            web_base_url="https://pr.example.com",
            web_cookie_secure=False,
        )
        database = Database(settings)
        async with database.transaction() as session:
            user = User(full_name="Race Subject", role=Role.TEAM_LEAD)
            session.add(user)
            await session.flush()
            user_id = user.id
        try:
            yield database, settings, user_id
        finally:
            await database.dispose()
    finally:
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        await admin.dispose()


@pytest.mark.asyncio
async def test_17_concurrent_redemption_yields_at_most_one_session(
    scratch: tuple[Database, Settings, uuid.UUID],
) -> None:
    """Eight browsers open the same link at once. Exactly one session exists.

    Each attempt runs in its **own transaction**, which is what makes this a real
    test: transactions are the unit the lock operates on, and running them on one
    session would serialise the work before PostgreSQL ever saw it.
    """
    database, settings, user_id = scratch

    async with database.transaction() as session:
        issued = await WebAuthService(session, settings).issue_login_link(user_id=user_id)
    token = issued.url.split("t=")[1]

    async def attempt() -> str | None:
        try:
            async with database.transaction() as session:
                result = await WebAuthService(session, settings).redeem_login_token(
                    token=token, user_agent="race", ip="127.0.0.1"
                )
                return result.token
        except ValidationError:
            # The expected loser: the row was taken, redeemed and committed by
            # somebody else, and this transaction read that state.
            return None

    outcomes = await asyncio.gather(*(attempt() for _ in range(ATTEMPTS)))
    winners = [token for token in outcomes if token is not None]

    assert len(winners) == 1, f"{len(winners)} of {ATTEMPTS} attempts minted a session"

    async with database.session() as session:
        sessions = (
            await session.execute(
                select(func.count())
                .select_from(WebSession)
                .where(WebSession.kind == WebSessionKind.SESSION)
            )
        ).scalar_one()
        login_tokens = (
            (
                await session.execute(
                    select(WebSession).where(WebSession.kind == WebSessionKind.LOGIN_TOKEN)
                )
            )
            .scalars()
            .all()
        )

    assert sessions == 1, "the database holds more than one session for one link"
    assert len(login_tokens) == 1
    assert login_tokens[0].redeemed_at is not None
    # The winner's token resolves; nothing else was handed out.
    async with database.session() as session:
        actor = await WebAuthService(session, settings).resolve_session(token=winners[0])
    assert actor is not None
    assert actor.user_id == user_id


@pytest.mark.asyncio
async def test_17b_a_second_link_and_a_race_still_yield_one_session_each(
    scratch: tuple[Database, Settings, uuid.UUID],
) -> None:
    """Asking for a new link kills the old one, even under concurrent redemption.

    The combination worth checking: somebody loses a link, asks for another, and
    then something replays the first. The first must be dead - otherwise a link
    sitting in a chat history stays usable forever, which is the whole reason
    issuing revokes the previous unredeemed token.
    """
    database, settings, user_id = scratch

    async with database.transaction() as session:
        first = await WebAuthService(session, settings).issue_login_link(user_id=user_id)
    async with database.transaction() as session:
        second = await WebAuthService(session, settings).issue_login_link(user_id=user_id)

    old_token = first.url.split("t=")[1]
    new_token = second.url.split("t=")[1]
    assert old_token != new_token

    async def attempt(token: str) -> bool:
        try:
            async with database.transaction() as session:
                await WebAuthService(session, settings).redeem_login_token(token=token)
                return True
        except ValidationError:
            return False

    # The revoked link, raced against itself, must never succeed.
    old_results = await asyncio.gather(*(attempt(old_token) for _ in range(4)))
    assert not any(old_results), "a revoked login link was redeemed"

    # The live one succeeds exactly once.
    new_results = await asyncio.gather(*(attempt(new_token) for _ in range(4)))
    assert sum(new_results) == 1

    async with database.session() as session:
        rows = (await session.execute(select(WebSession))).scalars().all()
    sessions = [row for row in rows if row.kind is WebSessionKind.SESSION]
    assert len(sessions) == 1
    # And the raw tokens are nowhere in the table.
    stored = {row.token_hash for row in rows}
    assert old_token not in stored and new_token not in stored
    assert hash_token(new_token) in stored


@pytest.mark.asyncio
async def test_the_redeem_path_actually_takes_a_row_lock(
    scratch: tuple[Database, Settings, uuid.UUID],
) -> None:
    """The statement PostgreSQL receives contains ``FOR UPDATE``.

    Belt and braces for the two tests above: they would also pass if the race
    simply never happened to interleave. This checks the mechanism rather than the
    outcome, by watching what the engine emits.
    """
    database, settings, user_id = scratch
    statements: list[str] = []

    from sqlalchemy import event

    engine = database.engine

    def record(conn: object, cursor: object, statement: str, *args: object) -> None:
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        async with database.transaction() as session:
            issued = await WebAuthService(session, settings).issue_login_link(user_id=user_id)
        statements.clear()
        async with database.transaction() as session:
            await WebAuthService(session, settings).redeem_login_token(
                token=issued.url.split("t=")[1]
            )
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)

    locking = [
        statement
        for statement in statements
        if "FOR UPDATE" in statement.upper() and "web_sessions" in statement
    ]
    assert locking, "redeem_login_token did not lock the login-token row"
    # And it waits rather than skipping: a skipped row would read as "no such
    # token" and the second browser would be told its link is invalid when the
    # truth is that somebody else is mid-redemption.
    assert not any("SKIP LOCKED" in statement.upper() for statement in locking)
