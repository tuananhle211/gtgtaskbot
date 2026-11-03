"""PR task codes need no approval: they count once the piece is done.

Revision ID: 0090
Revises: 0089
Create Date: 2026-11-03

Data only. Until now a Trưởng phòng PR approved each ``pr_point_claims`` row
and the points went to ``work_points`` dated the approval. From 2026-11-03
nobody approves: the codes count the moment the PR piece is done
(READY_TO_PUBLISH / PUBLISHED / MEASURED / ARCHIVED - "Hoàn thành" on the
board), dated that moment (``application/performance/pr_points_settle.py``).
``status`` keeps its values with a new meaning: PENDING = not counted yet,
APPROVED = counted.

Applied to what is there:

* a claim on a piece that is **not** done loses whatever it had counted
  (its ``pr_claim:<id>:<phase>`` rows) and is PENDING, decision cleared;
* a claim on a piece that **is** done and not counted yet (PENDING or an old
  REJECTED) is counted now: one ``work_points`` row per phase with codes,
  dated when the piece first entered a done stage (its transition history,
  else its ``updated_at``), in the person's PR department else their first
  seat - the rule ``put_points`` follows; the claim becomes APPROVED, decided
  at that date by nobody. A claim already counted keeps its row.

Downgrade
---------

Nothing to undo in the schema; the data stays as it is (the old code reads it
as decided claims).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0090"
down_revision: str | None = "0089"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DONE = ("READY_TO_PUBLISH", "PUBLISHED", "MEASURED", "ARCHIVED")
PHASES = ("BIEN_TAP", "THIET_KE", "DUNG")
_DONE_SQL = "('READY_TO_PUBLISH', 'PUBLISHED', 'MEASURED', 'ARCHIVED')"


def _items(raw: Any) -> dict[str, Any]:
    if isinstance(raw, str):
        raw = json.loads(raw)
    return raw if isinstance(raw, dict) else {}


def upgrade() -> None:
    bind = op.get_bind()
    # 1. Not done: take back what was counted, wait again.
    open_claims = [
        row[0]
        for row in bind.execute(
            sa.text(
                "SELECT cl.id FROM pr_point_claims cl"
                " JOIN pr_content_items c ON c.id = cl.content_id"
                f" WHERE c.workflow_stage NOT IN {_DONE_SQL}"
            )
        )
    ]
    for claim_id in open_claims:
        bind.execute(
            sa.text("DELETE FROM work_points WHERE source_key IN :keys").bindparams(
                sa.bindparam("keys", expanding=True)
            ),
            {"keys": [f"pr_claim:{claim_id}:{phase}" for phase in PHASES]},
        )
    bind.execute(
        sa.text(
            "UPDATE pr_point_claims SET status = 'PENDING', decided_at = NULL,"
            " decided_by_user_id = NULL, return_reason = NULL, decision_note = NULL"
            " WHERE content_id IN (SELECT id FROM pr_content_items"
            f" WHERE workflow_stage NOT IN {_DONE_SQL})"
        )
    )

    # 2. Done and not counted yet: count now, dated when the piece got done.
    rows = bind.execute(
        sa.text(
            "SELECT cl.id, cl.user_id, cl.items, c.code, c.title, c.updated_at,"
            " (SELECT min(e.created_at) FROM pr_content_transition_events e"
            "   WHERE e.content_id = c.id"
            f"   AND e.to_stage IN {_DONE_SQL} AND e.from_stage NOT IN {_DONE_SQL}) AS done_at"
            " FROM pr_point_claims cl JOIN pr_content_items c ON c.id = cl.content_id"
            f" WHERE c.workflow_stage IN {_DONE_SQL} AND cl.status <> 'APPROVED'"
        )
    ).all()
    for claim_id, user_id, raw, code, title, updated_at, done_at in rows:
        earned = done_at or updated_at or datetime.now(UTC)
        department = bind.execute(
            sa.text(
                "SELECT m.department_id FROM department_members m"
                " JOIN departments d ON d.id = m.department_id"
                " WHERE m.user_id = :user AND m.left_at IS NULL AND d.active"
                " ORDER BY CASE WHEN d.function = 'PR' THEN 0 ELSE 1 END, d.sort_order, d.code"
                " LIMIT 1"
            ),
            {"user": user_id},
        ).scalar()
        for phase, lines in _items(raw).items():
            if not lines:
                continue
            key = f"pr_claim:{claim_id}:{phase}"
            bind.execute(sa.text("DELETE FROM work_points WHERE source_key = :key"), {"key": key})
            bind.execute(
                sa.text(
                    "INSERT INTO work_points (id, source, source_key, user_id, department_id,"
                    " phase, items, points, earned_at, ref_code, ref_title, ref_link, created_at)"
                    " VALUES (:id, 'PR_CONTENT', :key, :user, :department, :phase,"
                    " CAST(:items AS JSONB), :points, :earned, :code, :title, :link, now())"
                ),
                {
                    "id": uuid.uuid4(),
                    "key": key,
                    "user": user_id,
                    "department": department,
                    "phase": phase,
                    "items": json.dumps(lines, ensure_ascii=False),
                    "points": sum(int(line.get("points") or 0) for line in lines),
                    "earned": earned,
                    "code": code,
                    "title": title,
                    "link": f"/tasks/{code}",
                },
            )
        bind.execute(
            sa.text(
                "UPDATE pr_point_claims SET status = 'APPROVED', decided_at = :at,"
                " decided_by_user_id = NULL, return_reason = NULL, decision_note = NULL"
                " WHERE id = :id"
            ),
            {"id": claim_id, "at": earned},
        )


def downgrade() -> None:
    """Data only: nothing to put back."""
