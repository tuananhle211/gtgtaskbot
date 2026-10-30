"""PR task codes ("điểm PR", 0085): who did a PR piece's work types its codes
once the stage is done, a Trưởng phòng PR approves, the points count.

* **Nội dung** (stage ``CONTENT``): the Biên kịch codes (N…), typed by the
  piece's owner, open once the content has passed its reviews (APPROVED or
  later);
* **Sản xuất** (stage ``PRODUCTION``): Design (T…) and/or Dựng (D…) codes,
  typed by the producer or anybody in PR who worked on it - each their own
  part - open once production is through its internal review
  (READY_TO_PUBLISH or later).

The codes, forms and their rules are ORD's own catalogue (``unit_design_codes``
of the ADS unit), so a point means the same everywhere. A Trưởng phòng PR (or
the OWNER / an ADMIN) approves - the points then go to ``work_points`` dated
the approval - or refuses with one of the three reasons; a refused claim is
edited and sent again. A Trưởng phòng's own claim stands at once. The PR
workflow is only read, never moved.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.orders.design_code_service import DesignCodeService
from meobot.application.performance.work_ledger import put_points
from meobot.application.units.directory import UnitDirectoryService
from meobot.core.time import utcnow
from meobot.db.models.org_unit import UnitDesignCode
from meobot.db.models.pr import PrContentItem
from meobot.db.models.user import User
from meobot.db.models.work_points import PrPointClaim
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.identity.models import Actor
from meobot.domain.orders.design_codes import (
    DesignItem,
    DesignPick,
    items_from_json,
    items_to_json,
    total_points,
)
from meobot.domain.orders.models import OrderNodeType, OrderReturnReason
from meobot.domain.orders.task_codes import TaskFormSpec, form_specs
from meobot.domain.org.models import DepartmentFunction, Position
from meobot.domain.performance.errors import (
    PrClaimDecidedError,
    PrPointsForbiddenError,
    PrPointsInvalidError,
    PrPointsNotFoundError,
)
from meobot.domain.performance.work_points import (
    STAGE_CONTENT,
    STAGE_LABELS,
    STAGE_PHASES,
    STAGE_PRODUCTION,
    WorkPointSource,
)
from meobot.domain.pr.models import PrWorkflowStage
from meobot.domain.units.models import UnitCode, UnitMembership

PENDING = "PENDING"
APPROVED = "APPROVED"
REJECTED = "REJECTED"

#: From when each stage may be claimed: the work is done.
CONTENT_DONE = frozenset(
    {
        PrWorkflowStage.APPROVED,
        PrWorkflowStage.PRODUCTION,
        PrWorkflowStage.INTERNAL_REVIEW,
        PrWorkflowStage.READY_TO_PUBLISH,
        PrWorkflowStage.PUBLISHED,
        PrWorkflowStage.MEASURED,
        PrWorkflowStage.ARCHIVED,
    }
)
PRODUCTION_DONE = frozenset(
    {
        PrWorkflowStage.READY_TO_PUBLISH,
        PrWorkflowStage.PUBLISHED,
        PrWorkflowStage.MEASURED,
        PrWorkflowStage.ARCHIVED,
    }
)
NOTE_MAX = 1000


def in_pr(membership: UnitMembership) -> bool:
    return membership.sees_all or membership.has(UnitCode.PR)


def reviews_pr(membership: UnitMembership) -> bool:
    """The OWNER, an ADMIN, or a head of a PR department."""
    return membership.sees_all or any(
        seat.function is DepartmentFunction.PR and seat.position is Position.HEAD
        for seat in membership.seats
    )


@dataclass(frozen=True, slots=True)
class ClaimView:
    claim: PrPointClaim
    name: str
    items: dict[str, tuple[DesignItem, ...]]
    decided_by: str | None = None
    content_code: str | None = None
    content_title: str | None = None


@dataclass(frozen=True, slots=True)
class StageView:
    stage: str
    label: str
    phases: tuple[str, ...]
    #: The work is done: codes may be typed.
    open: bool
    #: Why not, when it is not.
    waiting: str | None
    #: The viewer may type their codes here.
    may_claim: bool
    claims: tuple[ClaimView, ...] = ()


@dataclass(frozen=True, slots=True)
class ContentPoints:
    content_id: uuid.UUID
    code: str
    title: str
    stages: tuple[StageView, ...]
    may_review: bool


@dataclass(frozen=True, slots=True)
class Catalogue:
    codes: dict[str, tuple[UnitDesignCode, ...]] = field(default_factory=dict)
    forms: tuple[TaskFormSpec, ...] = ()


class PrPointService:
    def __init__(self, session: AsyncSession, audit: AuditService) -> None:
        self._session = session
        self._audit = audit
        self._directory = UnitDirectoryService(session)

    # --- reading -----------------------------------------------------------------

    async def catalogue(self) -> Catalogue:
        """ORD's live task codes of the three phases, and the forms."""
        unit = await self._directory.unit(UnitCode.ADS)
        settings = await self._directory.settings(UnitCode.ADS)
        rows = (
            await self._session.scalars(
                select(UnitDesignCode)
                .where(UnitDesignCode.unit_id == unit.id, UnitDesignCode.active.is_(True))
                .order_by(UnitDesignCode.sort_order, UnitDesignCode.code)
            )
        ).all()
        codes: dict[str, list[UnitDesignCode]] = {"BIEN_TAP": [], "THIET_KE": [], "DUNG": []}
        for row in rows:
            if row.phase in codes:
                codes[row.phase].append(row)
        return Catalogue(
            codes={phase: tuple(found) for phase, found in codes.items()},
            forms=form_specs(settings.task_forms),
        )

    async def content(self, content_id: uuid.UUID, membership: UnitMembership) -> ContentPoints:
        if not in_pr(membership):
            raise PrPointsForbiddenError("Chỉ thành viên PR xem được điểm PR.")
        content = await self._content(content_id)
        claims = (
            await self._session.scalars(
                select(PrPointClaim)
                .where(PrPointClaim.content_id == content.id)
                .order_by(PrPointClaim.submitted_at)
            )
        ).all()
        views = await self._views(claims)
        stages = tuple(
            self._stage(stage, content, membership, [v for v in views if v.claim.stage == stage])
            for stage in (STAGE_CONTENT, STAGE_PRODUCTION)
        )
        return ContentPoints(
            content_id=content.id,
            code=content.code,
            title=content.title,
            stages=stages,
            may_review=reviews_pr(membership),
        )

    async def queue(self, membership: UnitMembership, *, status: str | None) -> list[ClaimView]:
        """The claims a reviewer decides on (everybody's), or one's own."""
        if not in_pr(membership):
            raise PrPointsForbiddenError("Chỉ thành viên PR xem được điểm PR.")
        statement = select(PrPointClaim).order_by(PrPointClaim.submitted_at.desc()).limit(300)
        if not reviews_pr(membership):
            statement = statement.where(PrPointClaim.user_id == membership.user_id)
        if status:
            statement = statement.where(PrPointClaim.status == status)
        return await self._views((await self._session.scalars(statement)).all(), contents=True)

    # --- writing -----------------------------------------------------------------

    async def submit(
        self,
        *,
        actor: Actor,
        membership: UnitMembership,
        request_id: uuid.UUID,
        content_id: uuid.UUID,
        stage: str,
        picks: Mapping[str, Sequence[DesignPick]],
        note: str | None = None,
    ) -> ClaimView:
        if stage not in STAGE_PHASES:
            raise PrPointsInvalidError("Không có pha này.", reason="pr_points_stage_invalid")
        content = await self._content(content_id)
        view = self._stage(stage, content, membership, [])
        if not view.open:
            raise PrPointsInvalidError(view.waiting or "Chưa tới lúc.", reason="pr_points_not_open")
        if not view.may_claim or actor.user_id is None:
            raise PrPointsForbiddenError()
        unknown = set(picks) - set(STAGE_PHASES[stage])
        if unknown:
            raise PrPointsInvalidError(
                f"Pha {STAGE_LABELS[stage]} không nhận mã của {', '.join(sorted(unknown))}.",
                reason="pr_points_phase_invalid",
            )
        catalogue = await self.catalogue()
        unit = await self._directory.unit(UnitCode.ADS)
        design = DesignCodeService(self._session, directory=self._directory, audit=self._audit)
        existing = await self._session.scalar(
            select(PrPointClaim).where(
                PrPointClaim.content_id == content.id,
                PrPointClaim.stage == stage,
                PrPointClaim.user_id == actor.user_id,
            )
        )
        if existing is not None and existing.status == APPROVED:
            raise PrClaimDecidedError()
        kept = _claim_items(existing) if existing is not None else {}
        items: dict[str, tuple[DesignItem, ...]] = {}
        for phase in STAGE_PHASES[stage]:
            chosen = list(picks.get(phase) or ())
            if not chosen:
                continue
            items[phase] = await design.resolve(
                unit.id,
                chosen,
                keep=kept.get(phase, ()),
                phase=OrderNodeType(phase),
                forms=catalogue.forms,
            )
        if not any(items.values()):
            raise PrPointsInvalidError(
                "Chọn ít nhất một mã task công việc.", reason="pr_points_codes_required"
            )
        text = (note or "").strip() or None
        if text is not None and len(text) > NOTE_MAX:
            raise PrPointsInvalidError(f"Ghi chú tối đa {NOTE_MAX} ký tự.")
        now = utcnow()
        claim = existing or PrPointClaim(content_id=content.id, stage=stage, user_id=actor.user_id)
        claim.items = {phase: items_to_json(found) for phase, found in items.items()}
        claim.points = sum(total_points(found) for found in items.values())
        claim.note = text
        claim.status = PENDING
        claim.submitted_at = now
        claim.decided_by_user_id = None
        claim.decided_at = None
        claim.return_reason = None
        claim.decision_note = None
        if existing is None:
            self._session.add(claim)
        await self._session.flush()
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_POINTS_CLAIMED.value,
            result=AuditResult.SUCCESS,
            entity_type="pr_point_claim",
            entity_id=str(claim.id),
            after_data={"content": content.code, "stage": stage, "points": claim.points},
        )
        if reviews_pr(membership):
            # A Trưởng phòng's own codes stand at once.
            await self._approve(claim, content, actor)
        return (await self._views([claim]))[0]

    async def decide(
        self,
        *,
        actor: Actor,
        membership: UnitMembership,
        request_id: uuid.UUID,
        claim_id: uuid.UUID,
        approve: bool,
        reason: str | None = None,
        note: str | None = None,
    ) -> ClaimView:
        if not reviews_pr(membership):
            raise PrPointsForbiddenError("Chỉ Trưởng phòng PR duyệt điểm PR.")
        claim = await self._session.get(PrPointClaim, claim_id)
        if claim is None:
            raise PrPointsNotFoundError()
        if claim.status != PENDING:
            raise PrClaimDecidedError()
        content = await self._content(claim.content_id)
        if approve:
            await self._approve(claim, content, actor)
        else:
            try:
                OrderReturnReason(reason or "")
            except ValueError as error:
                raise PrPointsInvalidError(
                    "Chọn lý do không duyệt.", reason="return_reason_required"
                ) from error
            claim.status = REJECTED
            claim.return_reason = reason
            claim.decision_note = (note or "").strip() or None
            claim.decided_by_user_id = actor.user_id
            claim.decided_at = utcnow()
            await self._session.flush()
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_POINTS_DECIDED.value,
            result=AuditResult.SUCCESS,
            entity_type="pr_point_claim",
            entity_id=str(claim.id),
            after_data={"status": claim.status, "reason": claim.return_reason},
        )
        return (await self._views([claim]))[0]

    # --- helpers -----------------------------------------------------------------

    async def _approve(self, claim: PrPointClaim, content: PrContentItem, actor: Actor) -> None:
        now = utcnow()
        claim.status = APPROVED
        claim.decided_by_user_id = actor.user_id
        claim.decided_at = now
        for phase, items in _claim_items(claim).items():
            if not items:
                continue
            await put_points(
                self._session,
                source=WorkPointSource.PR_CONTENT.value,
                source_key=f"pr_claim:{claim.id}:{phase}",
                user_id=claim.user_id,
                phase=phase,
                items=items,
                earned_at=now,
                ref_code=content.code,
                ref_title=content.title,
                ref_link=f"/tasks/{content.code}",
            )
        await self._session.flush()

    def _stage(
        self,
        stage: str,
        content: PrContentItem,
        membership: UnitMembership,
        claims: list[ClaimView],
    ) -> StageView:
        done = CONTENT_DONE if stage == STAGE_CONTENT else PRODUCTION_DONE
        is_open = content.workflow_stage in done
        waiting = None
        if not is_open:
            waiting = (
                "Nhập mã sau khi nội dung được duyệt."
                if stage == STAGE_CONTENT
                else "Nhập mã sau khi sản xuất qua bước duyệt nội bộ."
            )
        user_id = membership.user_id
        if stage == STAGE_CONTENT:
            mine = user_id is not None and user_id in {content.owner_user_id}
        else:
            # The producer, or anybody in PR who worked on it.
            mine = user_id is not None and in_pr(membership)
        return StageView(
            stage=stage,
            label=STAGE_LABELS[stage],
            phases=STAGE_PHASES[stage],
            open=is_open,
            waiting=waiting,
            may_claim=bool(mine) and is_open,
            claims=tuple(claims),
        )

    async def _content(self, content_id: uuid.UUID) -> PrContentItem:
        content = await self._session.get(PrContentItem, content_id)
        if content is None:
            raise PrPointsNotFoundError()
        return content

    async def _views(
        self, claims: Sequence[PrPointClaim], *, contents: bool = False
    ) -> list[ClaimView]:
        people = {claim.user_id for claim in claims} | {
            claim.decided_by_user_id for claim in claims if claim.decided_by_user_id
        }
        names: dict[uuid.UUID, str] = {}
        if people:
            names = {
                row[0]: row[1] or "—"
                for row in (
                    await self._session.execute(
                        select(User.id, User.full_name).where(User.id.in_(people))
                    )
                ).all()
            }
        pieces: dict[uuid.UUID, tuple[str, str]] = {}
        if contents and claims:
            pieces = {
                row[0]: (row[1], row[2])
                for row in (
                    await self._session.execute(
                        select(PrContentItem.id, PrContentItem.code, PrContentItem.title).where(
                            PrContentItem.id.in_({claim.content_id for claim in claims})
                        )
                    )
                ).all()
            }
        return [
            ClaimView(
                claim=claim,
                name=names.get(claim.user_id, "—"),
                items=_claim_items(claim),
                decided_by=names.get(claim.decided_by_user_id)
                if claim.decided_by_user_id
                else None,
                content_code=pieces.get(claim.content_id, (None, None))[0],
                content_title=pieces.get(claim.content_id, (None, None))[1],
            )
            for claim in claims
        ]


def _claim_items(claim: PrPointClaim) -> dict[str, tuple[DesignItem, ...]]:
    raw = claim.items if isinstance(claim.items, dict) else {}
    return {phase: items_from_json(value) for phase, value in raw.items()}


__all__ = [
    "APPROVED",
    "PENDING",
    "REJECTED",
    "Catalogue",
    "ClaimView",
    "ContentPoints",
    "PrPointService",
    "StageView",
    "in_pr",
    "reviews_pr",
]
