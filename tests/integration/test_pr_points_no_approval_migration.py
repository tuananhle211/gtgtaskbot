"""0090: PR task codes need no approval - they count once the piece is done.

Runs the real migration on a scratch PostgreSQL database with three pieces:
one still in production holding an approved (counted) claim, one done with a
waiting claim and one done with an old refusal. After the upgrade the first
claim is taken back and waits, the two others are counted, dated when their
piece first got done, in the person's PR department.

    export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/postgres
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from tests.integration.test_dispatch_migrations import TEST_DATABASE_URL, upgrade_to

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="module"),
    pytest.mark.skipif(
        not TEST_DATABASE_URL,
        reason="Set MEOBOT_TEST_DATABASE_URL to run integration tests",
    ),
]

USER = uuid.uuid4()
BRAND = uuid.uuid4()
DEPT_PR = uuid.uuid4()
DEPT_OTHER = uuid.uuid4()
IN_PRODUCTION = uuid.uuid4()
DONE = uuid.uuid4()
DONE_REFUSED = uuid.uuid4()
CLAIM_OPEN = uuid.uuid4()
CLAIM_DONE = uuid.uuid4()
CLAIM_REFUSED = uuid.uuid4()
DONE_AT = datetime(2026, 10, 20, 3, 0, tzinfo=UTC)


def _line(code: str, points: int) -> dict[str, object]:
    return {"code": code, "code_id": str(uuid.uuid4()), "points": points, "quantity": 1}


async def _seed(engine: AsyncEngine) -> None:
    async with engine.begin() as c:
        await c.execute(
            text(
                "INSERT INTO users (id, full_name, role, status, active)"
                " VALUES (:id, 'Ánh Tuyết', 'EMPLOYEE', 'active', true)"
            ),
            {"id": USER},
        )
        await c.execute(
            text("INSERT INTO pr_brands (id, code, name) VALUES (:id, 'APM', 'Apexmed')"),
            {"id": BRAND},
        )
        for dept, code, function, order in (
            (DEPT_OTHER, "T89HC", "NONE", 1),
            (DEPT_PR, "T89PR", "PR", 9),
        ):
            await c.execute(
                text(
                    "INSERT INTO departments (id, code, name, function, active, sort_order)"
                    " VALUES (:id, :code, :code, :function, true, :order)"
                ),
                {"id": dept, "code": code, "function": function, "order": order},
            )
            await c.execute(
                text(
                    "INSERT INTO department_members (id, department_id, user_id, position)"
                    " VALUES (:id, :dept, :user, 'MEMBER')"
                ),
                {"id": uuid.uuid4(), "dept": dept, "user": USER},
            )
        for piece, code, stage in (
            (IN_PRODUCTION, "CNT-1", "PRODUCTION"),
            (DONE, "CNT-2", "PUBLISHED"),
            (DONE_REFUSED, "CNT-3", "READY_TO_PUBLISH"),
        ):
            await c.execute(
                text(
                    "INSERT INTO pr_content_items (id, code, title, brand_id, owner_user_id,"
                    " created_by_user_id, workflow_stage)"
                    " VALUES (:id, :code, :code, :brand, :user, :user, :stage)"
                ),
                {"id": piece, "code": code, "brand": BRAND, "user": USER, "stage": stage},
            )
        await c.execute(
            text(
                "INSERT INTO pr_content_transition_events (id, content_id, from_stage, to_stage,"
                " trigger, created_at) VALUES (:id, :piece, 'INTERNAL_REVIEW',"
                " 'READY_TO_PUBLISH', 'MANUAL', :at)"
            ),
            {"id": uuid.uuid4(), "piece": DONE, "at": DONE_AT},
        )
        claim = (
            "INSERT INTO pr_point_claims (id, content_id, stage, user_id, items, points,"
            " status, submitted_at, decided_at, return_reason)"
            " VALUES (:id, :piece, :stage, :user, CAST(:items AS JSONB), :points, :status,"
            " now(), :decided, :reason)"
        )
        await c.execute(
            text(claim),
            {
                "id": CLAIM_OPEN,
                "piece": IN_PRODUCTION,
                "stage": "PRODUCTION",
                "user": USER,
                "items": json.dumps({"THIET_KE": [_line("T0", 60)]}),
                "points": 60,
                "status": "APPROVED",
                "decided": DONE_AT,
                "reason": None,
            },
        )
        await c.execute(
            text(
                "INSERT INTO work_points (id, source, source_key, user_id, phase, items, points,"
                " earned_at) VALUES (:id, 'PR_CONTENT', :key, :user, 'THIET_KE', '[]', 60, now())"
            ),
            {"id": uuid.uuid4(), "key": f"pr_claim:{CLAIM_OPEN}:THIET_KE", "user": USER},
        )
        await c.execute(
            text(claim),
            {
                "id": CLAIM_DONE,
                "piece": DONE,
                "stage": "PRODUCTION",
                "user": USER,
                "items": json.dumps(
                    {"THIET_KE": [_line("T1", 15), _line("T2", 30)], "DUNG": [_line("D3", 240)]}
                ),
                "points": 285,
                "status": "PENDING",
                "decided": None,
                "reason": None,
            },
        )
        await c.execute(
            text(claim),
            {
                "id": CLAIM_REFUSED,
                "piece": DONE_REFUSED,
                "stage": "CONTENT",
                "user": USER,
                "items": json.dumps({"BIEN_TAP": [_line("N1", 15)]}),
                "points": 15,
                "status": "REJECTED",
                "decided": DONE_AT,
                "reason": "WRONG_CODES",
            },
        )


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def engine() -> AsyncIterator[AsyncEngine]:
    assert TEST_DATABASE_URL is not None
    base = make_url(TEST_DATABASE_URL)
    scratch = f"meobot_0090_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(
        base.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=None
    )
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{scratch}"'))
        url = base.set(database=scratch).render_as_string(hide_password=False)
        await upgrade_to(url, "0089")
        found = create_async_engine(url)
        try:
            await _seed(found)
            await upgrade_to(url, "0090")
            yield found
        finally:
            await found.dispose()
    finally:
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)'))
        await admin.dispose()


async def test_01_an_unfinished_piece_gives_its_points_back(engine: AsyncEngine) -> None:
    async with engine.connect() as c:
        status, decided = (
            await c.execute(
                text("SELECT status, decided_at FROM pr_point_claims WHERE id = :id"),
                {"id": CLAIM_OPEN},
            )
        ).one()
        assert (status, decided) == ("PENDING", None)
        left = await c.scalar(
            text("SELECT count(*) FROM work_points WHERE source_key LIKE :key"),
            {"key": f"pr_claim:{CLAIM_OPEN}:%"},
        )
        assert left == 0


async def test_02_done_pieces_count_dated_when_they_got_done(engine: AsyncEngine) -> None:
    async with engine.connect() as c:
        rows = (
            await c.execute(
                text(
                    "SELECT source_key, phase, points, earned_at, department_id, ref_code"
                    " FROM work_points WHERE user_id = :user ORDER BY source_key, phase"
                ),
                {"user": USER},
            )
        ).all()
        by_key = {row[0]: row for row in rows}
        design = by_key[f"pr_claim:{CLAIM_DONE}:THIET_KE"]
        assert design.points == 45
        assert design.earned_at == DONE_AT
        assert design.department_id == DEPT_PR
        assert design.ref_code == "CNT-2"
        assert by_key[f"pr_claim:{CLAIM_DONE}:DUNG"].points == 240
        # No transition history: dated by the piece's last update.
        assert by_key[f"pr_claim:{CLAIM_REFUSED}:BIEN_TAP"].points == 15
        statuses = dict(
            (
                await c.execute(
                    text("SELECT id, status FROM pr_point_claims WHERE id IN (:a, :b)"),
                    {"a": CLAIM_DONE, "b": CLAIM_REFUSED},
                )
            ).all()
        )
        assert statuses == {CLAIM_DONE: "APPROVED", CLAIM_REFUSED: "APPROVED"}
        reason = await c.scalar(
            text("SELECT return_reason FROM pr_point_claims WHERE id = :id"), {"id": CLAIM_REFUSED}
        )
        assert reason is None
