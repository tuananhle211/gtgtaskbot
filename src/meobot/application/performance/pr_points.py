"""PR task codes ("điểm PR", 0085): who did a PR piece's work types its codes,
the points count once the piece is done.

* **Order / Nội dung** (stage ``CONTENT``): the Biên kịch codes (N…), typed by
  the piece's owner;
* **Sản xuất** (stage ``PRODUCTION``): Design (T…) and/or Dựng (D…) codes,
  typed by the producer or anybody in PR who worked on it - each their own
  part.

Both stay open from the piece's creation (2026-10-31; only a cancelled piece
closes them), and the piece may not leave a phase without its codes
(:mod:`meobot.application.performance.pr_code_gate`).

The codes, forms and their rules are ORD's own catalogue (``unit_design_codes``
of the ADS unit), so a point means the same everywhere.

**No approval (2026-11-03).** Everybody types their own codes until the piece
is sent to internal review (``PR_MEMBER_EDIT_STAGES``); then only a Trưởng
phòng PR (or the OWNER / an ADMIN) edits or removes anybody's, until the piece
is done (``PR_HEAD_EDIT_STAGES``) - they review, they do not approve. When the
piece is done every claim goes to ``work_points``, dated that moment
(:mod:`meobot.application.performance.pr_points_settle`): ``PENDING`` = not
counted yet, ``APPROVED`` = counted. The PR workflow is only read here.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.orders.design_code_service import DesignCodeService
from meobot.application.performance.pr_points_settle import take_back
from meobot.application.units.directory import UnitDirectoryService
from meobot.core.time import utcnow
from meobot.db.models.org_unit import UnitDesignCode
from meobot.db.models.pr import PrContentItem
from meobot.db.models.user import User
from meobot.db.models.work_points import PrPointClaim
from meobot.domain.access.models import UserStatus
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.identity.models import Actor
from meobot.domain.orders.design_codes import (
    DesignItem,
    DesignPick,
    items_from_json,
    items_to_json,
    total_points,
)
from meobot.domain.orders.models import OrderNodeType
from meobot.domain.orders.task_codes import TaskFormSpec, form_specs
from meobot.domain.org.models import DepartmentFunction, Position
from meobot.domain.performance.errors import (
    PrPointsForbiddenError,
    PrPointsInvalidError,
    PrPointsNotFoundError,
)
from meobot.domain.performance.work_points import (
    PR_DONE_STAGES,
    PR_HEAD_EDIT_STAGES,
    PR_MEMBER_EDIT_STAGES,
    STAGE_CONTENT,
    STAGE_LABELS,
    STAGE_PHASES,
    STAGE_PRODUCTION,
)
from meobot.domain.pr.models import PrWorkflowStage
from meobot.domain.units.models import UnitCode, UnitMembership

#: Not counted yet (the piece is not done).
PENDING = "PENDING"
#: Counted: the piece is done, the points are in ``work_points``.
APPROVED = "APPROVED"
#: Before 2026-11-03 only (a refusal); read as not counted.
REJECTED = "REJECTED"
NOTE_MAX = 1000

LOCKED_FOR_MEMBERS = (
    "Đã gửi duyệt nội bộ: chỉ Trưởng phòng PR sửa được mã task đến khi task hoàn thành."
)
DONE_MESSAGE = "Task đã hoàn thành: mã task đã khoá và điểm đã cộng vào hiệu suất."
CANCELLED_MESSAGE = "Bài đã huỷ, không nhập mã được nữa."


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
    #: Somebody may still change the codes (not done, not cancelled).
    open: bool
    #: Why the viewer may not, when they may not.
    waiting: str | None
    #: The viewer may type their own codes here.
    may_claim: bool
    claims: tuple[ClaimView, ...] = ()
    #: A Trưởng phòng PR: may edit / remove anybody's codes here.
    may_edit_others: bool = False


@dataclass(frozen=True, slots=True)
class ContentPoints:
    content_id: uuid.UUID
    code: str
    title: str
    stages: tuple[StageView, ...]
    may_review: bool
    #: The piece is done: its codes are counted.
    done: bool = False


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
            done=content.workflow_stage in PR_DONE_STAGES,
        )

    async def queue(self, membership: UnitMembership, *, status: str | None) -> list[ClaimView]:
        """A Trưởng phòng PR: everybody's claims; anybody else: their own.
        ``status`` PENDING = not counted yet (old refusals too), APPROVED =
        counted."""
        if not in_pr(membership):
            raise PrPointsForbiddenError("Chỉ thành viên PR xem được điểm PR.")
        statement = select(PrPointClaim).order_by(PrPointClaim.submitted_at.desc()).limit(300)
        if not reviews_pr(membership):
            statement = statement.where(PrPointClaim.user_id == membership.user_id)
        if status == PENDING:
            statement = statement.where(PrPointClaim.status != APPROVED)
        elif status:
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
        user_id: uuid.UUID | None = None,
    ) -> ClaimView:
        """One's own codes for one stage, or - ``user_id`` - a Trưởng phòng PR
        editing somebody's. Saved as typed; nobody approves (2026-11-03)."""
        if stage not in STAGE_PHASES:
            raise PrPointsInvalidError("Không có pha này.", reason="pr_points_stage_invalid")
        if actor.user_id is None:
            raise PrPointsForbiddenError()
        content = await self._content(content_id)
        view = self._stage(stage, content, membership, [])
        target = user_id or actor.user_id
        if target == actor.user_id:
            allowed = view.may_claim
        else:
            allowed = view.may_edit_others
            if allowed and not await self._active(target):
                raise PrPointsInvalidError("Không tìm thấy người này.", reason="pr_points_person")
        if not allowed:
            if not view.open:
                raise PrPointsInvalidError(view.waiting or "Đã khoá.", reason="pr_points_not_open")
            if view.waiting:
                raise PrPointsInvalidError(view.waiting, reason="pr_points_locked")
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
                PrPointClaim.user_id == target,
            )
        )
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
        claim = existing or PrPointClaim(content_id=content.id, stage=stage, user_id=target)
        if existing is not None and existing.status == APPROVED:
            # Not reachable once the piece is done (frozen) - kept safe anyway.
            await take_back(self._session, existing)
        claim.items = {phase: items_to_json(found) for phase, found in items.items()}
        claim.points = sum(total_points(found) for found in items.values())
        claim.note = text
        claim.status = PENDING
        if existing is None or target == actor.user_id:
            claim.submitted_at = utcnow()
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
            after_data={
                "content": content.code,
                "stage": stage,
                "points": claim.points,
                "user_id": str(target),
                "by_head": target != actor.user_id,
            },
        )
        return (await self._views([claim]))[0]

    async def remove(
        self,
        *,
        actor: Actor,
        membership: UnitMembership,
        request_id: uuid.UUID,
        claim_id: uuid.UUID,
    ) -> None:
        """A Trưởng phòng PR takes somebody's codes off a piece (until it is
        done)."""
        claim = await self._session.get(PrPointClaim, claim_id)
        if claim is None:
            raise PrPointsNotFoundError()
        content = await self._content(claim.content_id)
        view = self._stage(claim.stage, content, membership, [])
        if not view.may_edit_others:
            if not view.open:
                raise PrPointsInvalidError(view.waiting or "Đã khoá.", reason="pr_points_not_open")
            raise PrPointsForbiddenError("Chỉ Trưởng phòng PR xoá được mã task của người khác.")
        before = {
            "content": content.code,
            "stage": claim.stage,
            "points": claim.points,
            "user_id": str(claim.user_id),
        }
        await take_back(self._session, claim)
        await self._session.delete(claim)
        await self._session.flush()
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_POINTS_REMOVED.value,
            result=AuditResult.SUCCESS,
            entity_type="pr_point_claim",
            entity_id=str(claim_id),
            before_data=before,
        )

    # --- helpers -----------------------------------------------------------------

    def _stage(
        self,
        stage: str,
        content: PrContentItem,
        membership: UnitMembership,
        claims: list[ClaimView],
    ) -> StageView:
        where = content.workflow_stage
        head = reviews_pr(membership)
        user_id = membership.user_id
        if stage == STAGE_CONTENT:
            mine = user_id is not None and user_id == content.owner_user_id
        else:
            # The producer, or anybody in PR who worked on it.
            mine = user_id is not None and in_pr(membership)
        is_open = where in PR_HEAD_EDIT_STAGES
        member_open = where in PR_MEMBER_EDIT_STAGES
        may_claim = bool(mine) and (member_open or (head and is_open))
        if where is PrWorkflowStage.CANCELLED:
            waiting: str | None = CANCELLED_MESSAGE
        elif where in PR_DONE_STAGES:
            waiting = DONE_MESSAGE
        elif not member_open and not head:
            waiting = LOCKED_FOR_MEMBERS
        else:
            waiting = None
        return StageView(
            stage=stage,
            label=STAGE_LABELS[stage],
            phases=STAGE_PHASES[stage],
            open=is_open,
            waiting=waiting,
            may_claim=may_claim,
            claims=tuple(claims),
            may_edit_others=head and is_open,
        )

    async def _active(self, user_id: uuid.UUID) -> bool:
        user = await self._session.get(User, user_id)
        return user is not None and user.deleted_at is None and user.status == UserStatus.ACTIVE

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
