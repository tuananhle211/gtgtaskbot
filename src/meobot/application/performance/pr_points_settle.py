"""A PR piece's task codes become points when the piece is done (2026-11-03).

No Trưởng phòng approval any more: every claim of the piece is written to
``work_points`` the moment the piece enters a done stage (``PR_DONE_STAGES``:
"Hoàn thành" on the board), dated that moment, and the claim is marked
``APPROVED`` (= "Đã cộng điểm"). Should the piece leave the done stages again
(an undo), the points are taken back and the claims are ``PENDING`` again.

Asked by ``PrWorkflowService.apply`` - the one place a piece's stage moves.
Idempotent: a claim already counted is left alone.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.performance.work_ledger import drop_points, put_points
from meobot.core.time import utcnow
from meobot.db.models.pr import PrContentItem
from meobot.db.models.work_points import PrPointClaim
from meobot.domain.orders.design_codes import items_from_json
from meobot.domain.performance.work_points import (
    PR_DONE_STAGES,
    STAGE_PHASES,
    WorkPointSource,
)

PENDING = "PENDING"
APPROVED = "APPROVED"


def claim_key(claim: PrPointClaim, phase: str) -> str:
    return f"pr_claim:{claim.id}:{phase}"


async def take_back(session: AsyncSession, claim: PrPointClaim) -> None:
    """Drop whatever the claim had counted; it waits for the piece again."""
    for phase in STAGE_PHASES.get(claim.stage, ()):
        await drop_points(session, claim_key(claim, phase))
    claim.status = PENDING
    claim.decided_at = None
    claim.decided_by_user_id = None
    claim.return_reason = None
    claim.decision_note = None


async def settle_claims(session: AsyncSession, content: PrContentItem) -> None:
    done = content.workflow_stage in PR_DONE_STAGES
    claims = (
        await session.scalars(select(PrPointClaim).where(PrPointClaim.content_id == content.id))
    ).all()
    now = utcnow()
    for claim in claims:
        if done and claim.status != APPROVED:
            raw = claim.items if isinstance(claim.items, dict) else {}
            for phase, value in raw.items():
                items = items_from_json(value)
                if not items:
                    continue
                await put_points(
                    session,
                    source=WorkPointSource.PR_CONTENT.value,
                    source_key=claim_key(claim, phase),
                    user_id=claim.user_id,
                    phase=phase,
                    items=items,
                    earned_at=now,
                    ref_code=content.code,
                    ref_title=content.title,
                    ref_link=f"/tasks/{content.code}",
                )
            claim.status = APPROVED
            claim.decided_at = now
            claim.decided_by_user_id = None
            claim.return_reason = None
            claim.decision_note = None
        elif not done and claim.status == APPROVED:
            await take_back(session, claim)
    await session.flush()


__all__ = ["APPROVED", "PENDING", "claim_key", "settle_claims", "take_back"]
