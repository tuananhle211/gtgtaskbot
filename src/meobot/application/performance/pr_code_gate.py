"""Task codes before a PR piece changes phase (2026-10-31).

The codes of each phase may be typed at any time (``PrPointService``), but a
piece may not move on while the phase it leaves has none:

* sending the script for review (IDEA / BRIEFING / SCRIPTING -> a review) and
  starting production (APPROVED -> PRODUCTION) need the "Order (Nội dung)"
  codes;
* handing production in (PRODUCTION -> INTERNAL_REVIEW) needs the "Sản xuất"
  codes (Design and / or Dựng).

"Codes" = somebody's claim on that stage, waiting or approved - a refused one
does not count. Asked by ``PrWorkflowService.apply``, the one path every client
(web, Telegram, bulk) takes, and by the task page to grey the button out. An
undo is never asked. Switch: the PR unit's ``pr_task_codes_required`` (default
on); a database without a PR unit asks nothing.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.db.models.org_unit import OrgUnit
from meobot.db.models.work_points import PrPointClaim
from meobot.domain.performance.errors import PrTaskCodesRequiredError
from meobot.domain.performance.work_points import STAGE_CONTENT, STAGE_PRODUCTION
from meobot.domain.pr.models import PrWorkflowStage
from meobot.domain.units.models import UnitCode, UnitSettings

_DRAFTING = frozenset({PrWorkflowStage.IDEA, PrWorkflowStage.BRIEFING, PrWorkflowStage.SCRIPTING})
_REVIEWS = frozenset(
    {
        PrWorkflowStage.AI_REVIEW,
        PrWorkflowStage.TEAM_LEAD_REVIEW,
        PrWorkflowStage.HEAD_REVIEW,
        PrWorkflowStage.APPROVED,
    }
)
#: Claims that count: waiting for the Trưởng phòng, or approved.
_LIVE = ("PENDING", "APPROVED")

MESSAGES: dict[str, str] = {
    STAGE_CONTENT: (
        "Chưa chọn mã task công việc pha Order (Nội dung). Người phụ trách chọn mã ở mục "
        '"Điểm hiệu suất (PR)" của bài rồi mới chuyển tiếp được.'
    ),
    STAGE_PRODUCTION: (
        "Chưa chọn mã task công việc pha Sản xuất (Design / Dựng). Chọn mã ở mục "
        '"Điểm hiệu suất (PR)" của bài rồi mới gửi duyệt được.'
    ),
}


def stage_needed(before: PrWorkflowStage, target: PrWorkflowStage) -> str | None:
    """The points stage whose codes the move ``before -> target`` needs."""
    if before in _DRAFTING and target in _REVIEWS:
        return STAGE_CONTENT
    if before is PrWorkflowStage.APPROVED and target is PrWorkflowStage.PRODUCTION:
        return STAGE_CONTENT
    if before is PrWorkflowStage.PRODUCTION and target is PrWorkflowStage.INTERNAL_REVIEW:
        return STAGE_PRODUCTION
    return None


async def codes_required(session: AsyncSession) -> bool:
    settings = await session.scalar(select(OrgUnit.settings).where(OrgUnit.code == UnitCode.PR))
    if settings is None:
        return False
    return UnitSettings.model_validate(settings).pr_task_codes_required


async def stages_with_codes(session: AsyncSession, content_id: uuid.UUID) -> frozenset[str]:
    found = await session.scalars(
        select(PrPointClaim.stage)
        .where(PrPointClaim.content_id == content_id, PrPointClaim.status.in_(_LIVE))
        .distinct()
    )
    return frozenset(found.all())


async def missing_codes(
    session: AsyncSession,
    content_id: uuid.UUID,
    before: PrWorkflowStage,
    target: PrWorkflowStage,
) -> str | None:
    """Why the move may not happen yet (the message), else None."""
    stage = stage_needed(before, target)
    if stage is None or not await codes_required(session):
        return None
    if stage in await stages_with_codes(session, content_id):
        return None
    return MESSAGES[stage]


async def require_codes(
    session: AsyncSession,
    content_id: uuid.UUID,
    before: PrWorkflowStage,
    target: PrWorkflowStage,
) -> None:
    message = await missing_codes(session, content_id, before, target)
    if message is not None:
        stage = stage_needed(before, target)
        assert stage is not None
        raise PrTaskCodesRequiredError(message, stage=stage)


__all__ = [
    "MESSAGES",
    "codes_required",
    "missing_codes",
    "require_codes",
    "stage_needed",
    "stages_with_codes",
]
