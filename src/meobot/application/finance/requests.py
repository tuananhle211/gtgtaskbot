"""Tài chính > Hồ sơ chi (đợt 3; meeting answers F1-F8, P5, P6, B5, M1, M2, M5).

Anybody files one (P6 B) for a pháp nhân, a phòng ban, maybe a cơ sở, a budget
code and an amount. Its route is the code's (``finance_codes.route``, kept on
the request as sent; F2 adds PTGĐ / CEO above the configured amount):

1. **Duyệt đề nghị mua sắm** - the code's purchase approvals, in order;
2. **Thực hiện mua sắm & nghiệm thu** - the code's executor records the quote,
   order, handover and acceptance (F3: an execution step, not an approval);
   the spending is realized here (B5);
3. **Duyệt thanh toán** - the payment approvals (Trưởng phòng, GĐ khối, the
   accountant in charge, Kế toán trưởng, PTGĐ / CEO as the code says);
4. **Chi tiền** - Quỹ cơ sở (a request of a cơ sở) / Thủ quỹ VP records "Đã
   chi" with the transfer / cash slip (M1). No OTP (M5 C).

Each step has ONE approver (F7: :meth:`FinanceRoleService.holder`, with its
stand-in). A step whose role the requester holds goes to the next level up
(F6: Trưởng phòng -> GĐ khối -> Phòng Tài chính -> Kế toán trưởng -> PTGĐ /
CEO, else another Ban giám đốc member). A role nobody holds yet leaves the
step waiting with a note ("Cấu hình > Vai trò tài chính"); it is looked up
again on every read. OWNER / ADMIN see everything but act only when they hold
the role (P5 A). "Không duyệt" ends the request; "Trả lại" sends it back to
the requester, whose resubmission starts again. Papers never block (F5 B):
missing ones are flagged and, once paid, due within 7 days. Files go to the
Google Drive folder set on "Cấu hình" (M2) when the server can reach Drive,
else they are kept in the database.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from meobot.application.audit_service import AuditService
from meobot.application.finance.access import FinanceAccess
from meobot.application.finance.budgets import BudgetLine, FinanceBudgetService
from meobot.application.finance.codes import FinanceCodeService
from meobot.application.finance.roles import FinanceRoleService
from meobot.application.hr.files import sniff
from meobot.application.notification_router import NotificationRouter, RouteRequest
from meobot.application.org.seats import load_seats
from meobot.application.recipient_resolver import RecipientResolver
from meobot.application.user_notification_service import UserNotificationService
from meobot.core.config import Settings
from meobot.core.errors import ConflictError
from meobot.core.logging import get_logger
from meobot.core.time import utcnow
from meobot.db.models.department import Department
from meobot.db.models.finance_spend import (
    FinanceCode,
    FinanceDocument,
    FinanceRequest,
    FinanceRequestEvent,
    FinanceRequestStep,
)
from meobot.db.models.legal_entity import LegalEntity
from meobot.db.models.user import User
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.finance.errors import (
    FinanceForbiddenError,
    FinanceInvalidError,
    FinanceNotFoundError,
)
from meobot.domain.finance.spend import (
    DOCUMENT_LABELS,
    PHASE_LABELS,
    ROLE_LABELS,
    DocumentKind,
    FinanceRole,
    Phase,
    RequestStatus,
    Route,
    StepStatus,
    cash_role,
    escalate,
)
from meobot.domain.identity.models import Actor, Role
from meobot.domain.notifications.models import NotificationEvent
from meobot.domain.org.models import BRANCHES, Position
from meobot.integrations.google.drive import DriveClient, GoogleDriveClient

logger = get_logger(__name__)

MAX_AMOUNT = Decimal("100000000000")
MAX_FILE_BYTES = 10 * 1024 * 1024
#: Papers missing when paid are due within (F5 B, answer 9).
DOCS_GRACE_DAYS = 7
OFFICE_TYPES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
}
APPROVALS = (Phase.PURCHASE.value, Phase.PAYMENT.value)
#: Org-chart levels skipped when nobody holds them (assigned roles wait).
ORG_LEVELS = (FinanceRole.DEPT_HEAD.value, FinanceRole.BLOCK_DIRECTOR.value)


class FinanceRequestMovedError(ConflictError):
    code = "finance_request_moved"

    def __init__(self) -> None:
        super().__init__(
            "Hồ sơ đã được người khác xử lý. Tải lại để xem.", details={"reason": self.code}
        )


@dataclass(slots=True)
class RequestView:
    request: FinanceRequest
    steps: list[FinanceRequestStep]
    events: list[FinanceRequestEvent]
    documents: list[FinanceDocument]
    names: dict[uuid.UUID, str]
    missing: list[DocumentKind]
    budget: BudgetLine | None
    can_act: list[str] = field(default_factory=list)
    open_step: FinanceRequestStep | None = None
    code_name: str = ""
    entity_name: str = ""
    department_name: str = ""


def _money(value: object) -> Decimal:
    text = str(value).replace(".", "").replace(",", "").replace(" ", "").strip()
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError) as error:
        raise FinanceInvalidError(
            "amount_invalid", "Số tiền phải là số.", field="amount"
        ) from error
    if number <= 0 or number > MAX_AMOUNT:
        raise FinanceInvalidError("amount_invalid", "Số tiền không hợp lệ.", field="amount")
    return number


class FinanceRequestService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        audit: AuditService,
        *,
        drive: DriveClient | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._audit = audit
        self._drive = drive
        self._roles = FinanceRoleService(session, settings, audit)

    # --- filing ------------------------------------------------------------------------------

    async def create(
        self,
        *,
        actor: Actor,
        access: FinanceAccess,
        request_id: uuid.UUID,
        legal_entity_id: uuid.UUID,
        department_id: uuid.UUID | None,
        apm_branch: str | None,
        budget_code: str,
        title: str,
        description: str,
        vendor: str | None,
        payee: str | None,
        amount: str,
        budget_month: date,
        needed_on: date | None,
    ) -> FinanceRequest:
        access.require("requests", "Bạn chưa lập được hồ sơ chi.")
        assert actor.user_id is not None
        route, row = await self._checked(
            legal_entity_id=legal_entity_id,
            department_id=department_id,
            apm_branch=apm_branch,
            budget_code=budget_code,
            title=title,
        )
        value = _money(amount)
        request = FinanceRequest(
            code=await self._next_code(),
            requester_id=actor.user_id,
            department_id=department_id,
            legal_entity_id=legal_entity_id,
            apm_branch=apm_branch,
            budget_code=row.code,
            title=title.strip(),
            description=(description or "").strip()[:5000],
            vendor=(vendor or "").strip()[:300] or None,
            payee=(payee or "").strip()[:500] or None,
            amount=value,
            budget_month=budget_month.replace(day=1),
            needed_on=needed_on,
            status=RequestStatus.PENDING.value,
            route=(await self._with_threshold(route, value)).as_value(),
            submitted_at=utcnow(),
            version=1,
        )
        self._session.add(request)
        await self._session.flush()
        self._event(request, actor, "submitted", None)
        await self._plan(request, round_=1)
        await self._advance(request, actor)
        await self._record(actor, request_id, request, "submitted")
        return request

    async def _checked(
        self,
        *,
        legal_entity_id: uuid.UUID,
        department_id: uuid.UUID | None,
        apm_branch: str | None,
        budget_code: str,
        title: str,
    ) -> tuple[Route, FinanceCode]:
        if await self._session.get(LegalEntity, legal_entity_id) is None:
            raise FinanceInvalidError(
                "legal_entity_invalid", "Chọn pháp nhân.", field="legal_entity_id"
            )
        if department_id is not None and await self._session.get(Department, department_id) is None:
            raise FinanceInvalidError(
                "department_invalid", "Phòng ban không có.", field="department_id"
            )
        if apm_branch is not None and apm_branch not in BRANCHES:
            raise FinanceInvalidError("branch_invalid", "Cơ sở không có.", field="apm_branch")
        row = await self._session.get(FinanceCode, budget_code)
        if row is None or not row.leaf or not row.active:
            raise FinanceInvalidError(
                "finance_code_invalid", "Chọn một mã ngân sách chi tiết.", field="budget_code"
            )
        route = Route.from_value(row.route)
        if route.empty:
            raise FinanceInvalidError(
                "finance_code_no_route",
                f"Mã {row.code} chưa có tuyến duyệt: nhờ Kế toán trưởng cấu hình "
                "ở Tài chính > Cấu hình.",
                field="budget_code",
            )
        text = title.strip()
        if not text or len(text) > 300:
            raise FinanceInvalidError("title_invalid", "Nội dung chi 1-300 ký tự.", field="title")
        return route, row

    async def _with_threshold(self, route: Route, amount: Decimal) -> Route:
        threshold = (await FinanceCodeService(self._session, self._audit).spend_settings())[
            "ceo_threshold"
        ]
        if threshold is None or amount <= threshold or route.no_approval:
            return route
        if FinanceRole.CEO in route.purchase or FinanceRole.CEO in route.payment:
            return route
        if route.purchase:
            return replace(route, purchase=(*route.purchase, FinanceRole.CEO))
        return replace(route, payment=(*route.payment, FinanceRole.CEO))

    async def _next_code(self) -> str:
        today = utcnow().date()
        prefix = f"DC-{today:%y%m}-"
        count = int(
            await self._session.scalar(
                select(func.count()).where(FinanceRequest.code.like(f"{prefix}%"))
            )
            or 0
        )
        return f"{prefix}{count + 1:04d}"

    async def _plan(self, request: FinanceRequest, *, round_: int) -> None:
        route = Route.from_value(request.route)
        steps: list[tuple[Phase, FinanceRole]] = []
        if not route.no_approval:
            steps += [(Phase.PURCHASE, role) for role in route.purchase]
            if route.executor is not None:
                steps.append((Phase.EXECUTE, route.executor))
            steps += [(Phase.PAYMENT, role) for role in route.payment]
        payer = cash_role(route, request.apm_branch) or FinanceRole.CASHIER_VP
        steps.append((Phase.CASH, payer))
        for position, (phase, role) in enumerate(steps):
            self._session.add(
                FinanceRequestStep(
                    request_id=request.id,
                    round=round_,
                    phase=phase.value,
                    position=position,
                    role=role.value,
                    status=StepStatus.WAITING.value,
                )
            )
        await self._session.flush()

    # --- moving along --------------------------------------------------------------------------

    async def _steps(
        self, request: FinanceRequest, *, every_round: bool = False
    ) -> list[FinanceRequestStep]:
        statement = select(FinanceRequestStep).where(FinanceRequestStep.request_id == request.id)
        if not every_round:
            current = await self._session.scalar(
                select(func.max(FinanceRequestStep.round)).where(
                    FinanceRequestStep.request_id == request.id
                )
            )
            statement = statement.where(FinanceRequestStep.round == (current or 1))
        return list(
            (
                await self._session.scalars(
                    statement.order_by(FinanceRequestStep.round, FinanceRequestStep.position)
                )
            ).all()
        )

    async def _resolve(
        self, request: FinanceRequest, step: FinanceRequestStep, steps: Sequence[FinanceRequestStep]
    ) -> None:
        """Who acts on ``step`` (F7), going up a level when it is the requester (F6)."""
        role = FinanceRole(step.role)
        holder = await self._roles.holder(
            role,
            requester=request.requester_id,
            legal_entity_id=request.legal_entity_id,
            apm_branch=request.apm_branch,
        )
        approver, standin, remark = holder.approver, holder.standin, None
        if step.phase in APPROVALS and approver == request.requester_id:
            later = [
                FinanceRole(item.role)
                for item in steps
                if item.phase == step.phase and item.position > step.position
            ]
            current = role
            approver, standin = None, None
            while approver is None or approver == request.requester_id:
                upper = escalate(current, later)
                if upper is None:
                    board = await self._roles.board(request.requester_id)
                    approver = board[0] if board else None
                    remark = (
                        f"Người lập giữ vai trò {ROLE_LABELS[role]}: "
                        "chuyển một thành viên Ban giám đốc"
                    )
                    break
                found = await self._roles.holder(
                    upper,
                    requester=request.requester_id,
                    legal_entity_id=request.legal_entity_id,
                    apm_branch=request.apm_branch,
                )
                approver, standin = found.approver, found.standin
                remark = (
                    f"Người lập giữ vai trò {ROLE_LABELS[role]}: chuyển lên {ROLE_LABELS[upper]}"
                )
                current = upper
        if standin == request.requester_id and step.phase in APPROVALS:
            standin = None
        step.approver_id, step.standin_id = approver, standin
        step.remark = (
            remark
            if approver
            else (
                f"Chưa có người giữ vai trò {ROLE_LABELS[role]}: "
                "gán ở Tài chính > Cấu hình > Vai trò tài chính"
            )
        )

    async def _advance(self, request: FinanceRequest, actor: Actor | None) -> None:
        steps = await self._steps(request)
        for step in steps:
            if step.status != StepStatus.WAITING.value:
                continue
            step.status = StepStatus.PENDING.value
            step.opened_at = utcnow()
            await self._resolve(request, step, steps)
            if _approver(step) is None and step.role in ORG_LEVELS:
                # Nobody on that level of the org chart (no GĐ khối for the
                # department...): skipped, as the HR requests do.
                step.status = StepStatus.SKIPPED.value
                step.remark = f"Không có {ROLE_LABELS[FinanceRole(step.role)]} trong sơ đồ tổ chức"
                continue
            request.phase = step.phase
            await self._session.flush()
            await self._notify_open(request, step)
            return
        request.phase = None
        await self._session.flush()

    async def _refresh_open(
        self, request: FinanceRequest, steps: Sequence[FinanceRequestStep]
    ) -> None:
        """A step that found nobody looks again (a role may have been given since)."""
        for step in steps:
            if step.status == StepStatus.PENDING.value and step.approver_id is None:
                await self._resolve(request, step, steps)
                if _approver(step) is not None:
                    await self._session.flush()
                    await self._notify_open(request, step)

    @staticmethod
    def _may_act(step: FinanceRequestStep, actor: Actor) -> bool:
        return actor.user_id is not None and actor.user_id in (step.approver_id, step.standin_id)

    async def act(
        self,
        *,
        actor: Actor,
        access: FinanceAccess,
        request_id: uuid.UUID,
        finance_request_id: uuid.UUID,
        action: str,
        note: str | None,
        step_id: int | None,
        purchase_note: str | None = None,
    ) -> FinanceRequest:
        request = await self._visible(finance_request_id, actor, access)
        text = (note or "").strip()[:2000] or None
        if action == "cancel":
            return await self._cancel(request, actor, access, request_id, text)
        if action == "resubmit":
            return await self._resubmit(request, actor, request_id)
        if request.status != RequestStatus.PENDING.value:
            raise FinanceRequestMovedError()
        steps = await self._steps(request)
        await self._refresh_open(request, steps)
        step = next((item for item in steps if item.status == StepStatus.PENDING.value), None)
        if step is None or (step_id is not None and step.id != step_id):
            raise FinanceRequestMovedError()
        if not self._may_act(step, actor):
            raise FinanceForbiddenError("Bước này không chờ bạn.")
        phase = Phase(step.phase)
        now = utcnow()
        if action in ("reject", "return"):
            if phase not in (Phase.PURCHASE, Phase.PAYMENT):
                raise FinanceInvalidError("action_invalid", "Bước này không duyệt / trả lại được.")
            if not text:
                raise FinanceInvalidError("note_required", "Nhập lý do.", field="note")
            step.status = (StepStatus.REJECTED if action == "reject" else StepStatus.RETURNED).value
            step.decided_by_user_id, step.decided_at, step.note = actor.user_id, now, text
            for other in steps:
                if (
                    other.status in (StepStatus.WAITING.value, StepStatus.PENDING.value)
                    and other is not step
                ):
                    other.status = StepStatus.CLOSED.value
            request.status = (
                RequestStatus.REJECTED if action == "reject" else RequestStatus.RETURNED
            ).value
            request.phase = None
            request.decided_at = now
            self._event(request, actor, action, text)
            await self._session.flush()
            await self._notify_requester(
                request,
                "Hồ sơ chi không được duyệt"
                if action == "reject"
                else "Hồ sơ chi bị trả lại để sửa",
                f"{_who(actor)} ({PHASE_LABELS[phase].lower()}): {text}",
            )
        elif action == "approve":
            if phase not in (Phase.PURCHASE, Phase.PAYMENT):
                raise FinanceInvalidError("action_invalid", "Bước này không phải bước duyệt.")
            self._done(step, actor, text, now)
            self._event(request, actor, f"approved:{step.role}", text)
            await self._advance(request, actor)
        elif action == "execute":
            if phase is not Phase.EXECUTE:
                raise FinanceInvalidError("action_invalid", "Bước này không phải bước mua sắm.")
            record = (purchase_note or "").strip()
            if not record:
                raise FinanceInvalidError(
                    "purchase_note_required",
                    "Ghi báo giá / đơn hàng / bàn giao, nghiệm thu.",
                    field="purchase_note",
                )
            request.purchase_note = record[:5000]
            request.realized_at = now  # B5: a purchase is realized at acceptance.
            self._done(step, actor, text, now)
            self._event(request, actor, "executed", record[:500])
            await self._advance(request, actor)
        elif action == "pay":
            if phase is not Phase.CASH:
                raise FinanceInvalidError("action_invalid", "Bước này không phải bước chi tiền.")
            proofs = await self._session.scalar(
                select(func.count()).where(
                    FinanceDocument.request_id == request.id,
                    FinanceDocument.kind == DocumentKind.PAYMENT_PROOF.value,
                )
            )
            if not proofs:
                raise FinanceInvalidError(
                    "payment_proof_required",
                    "Tải lên ảnh UNC / phiếu chi trước khi bấm Đã chi.",
                )
            self._done(step, actor, text, now)
            request.status = RequestStatus.PAID.value
            request.phase = None
            request.paid_at = now
            request.realized_at = request.realized_at or now
            if await self.missing(request):
                request.docs_due_on = now.date() + timedelta(days=DOCS_GRACE_DAYS)
            self._event(request, actor, "paid", text)
            await self._session.flush()
            await self._notify_requester(
                request,
                "Hồ sơ chi đã được chi tiền",
                f"{_who(actor)} đã chi {_vnd(request.amount)}.",
            )
        else:
            raise FinanceInvalidError("action_invalid", "Thao tác không có.")
        request.version += 1
        await self._session.flush()
        await self._record(actor, request_id, request, action)
        return request

    @staticmethod
    def _done(step: FinanceRequestStep, actor: Actor, note: str | None, now: object) -> None:
        step.status = StepStatus.DONE.value
        step.decided_by_user_id = actor.user_id
        step.decided_at = now  # type: ignore[assignment]
        step.note = note

    async def _cancel(
        self,
        request: FinanceRequest,
        actor: Actor,
        access: FinanceAccess,
        request_id: uuid.UUID,
        note: str | None,
    ) -> FinanceRequest:
        own = actor.user_id == request.requester_id
        if request.status not in (RequestStatus.PENDING.value, RequestStatus.RETURNED.value):
            raise FinanceRequestMovedError()
        if not own and actor.role not in (Role.OWNER, Role.ADMIN):
            raise FinanceForbiddenError("Chỉ người lập huỷ được hồ sơ chi.")
        if request.realized_at is not None and not access.view_requests:
            raise FinanceInvalidError(
                "already_realized", "Hồ sơ đã nghiệm thu, nhờ Kế toán huỷ.", field="note"
            )
        for step in await self._steps(request):
            if step.status in (StepStatus.WAITING.value, StepStatus.PENDING.value):
                step.status = StepStatus.CLOSED.value
        request.status = RequestStatus.CANCELLED.value
        request.phase = None
        request.decided_at = utcnow()
        request.version += 1
        self._event(request, actor, "cancelled", note)
        await self._session.flush()
        await self._record(actor, request_id, request, "cancel")
        return request

    async def _resubmit(
        self, request: FinanceRequest, actor: Actor, request_id: uuid.UUID
    ) -> FinanceRequest:
        if actor.user_id != request.requester_id:
            raise FinanceForbiddenError("Chỉ người lập gửi lại được.")
        if request.status != RequestStatus.RETURNED.value:
            raise FinanceRequestMovedError()
        latest = max((step.round for step in await self._steps(request)), default=1)
        request.status = RequestStatus.PENDING.value
        request.decided_at = None
        request.realized_at = None
        request.submitted_at = utcnow()
        request.version += 1
        self._event(request, actor, "resubmitted", None)
        await self._plan(request, round_=latest + 1)
        await self._advance(request, actor)
        await self._record(actor, request_id, request, "resubmit")
        return request

    async def edit(
        self,
        *,
        actor: Actor,
        access: FinanceAccess,
        request_id: uuid.UUID,
        finance_request_id: uuid.UUID,
        changes: dict[str, object],
    ) -> FinanceRequest:
        """The requester edits a returned request before sending it again."""
        request = await self._visible(finance_request_id, actor, access)
        if actor.user_id != request.requester_id or request.status != RequestStatus.RETURNED.value:
            raise FinanceForbiddenError("Chỉ sửa được hồ sơ của mình đang bị trả lại.")
        merged = {
            "legal_entity_id": changes.get("legal_entity_id", request.legal_entity_id),
            "department_id": changes.get("department_id", request.department_id),
            "apm_branch": changes.get("apm_branch", request.apm_branch),
            "budget_code": changes.get("budget_code", request.budget_code),
            "title": changes.get("title", request.title),
        }
        route, row = await self._checked(**merged)  # type: ignore[arg-type]
        request.legal_entity_id = merged["legal_entity_id"]  # type: ignore[assignment]
        request.department_id = merged["department_id"]  # type: ignore[assignment]
        request.apm_branch = merged["apm_branch"]  # type: ignore[assignment]
        request.budget_code = row.code
        request.title = str(merged["title"]).strip()
        for name in ("description", "vendor", "payee"):
            if name in changes:
                setattr(
                    request,
                    name,
                    (str(changes[name] or "").strip() or None)
                    if name != "description"
                    else str(changes[name] or "").strip(),
                )
        if "amount" in changes:
            request.amount = _money(changes["amount"])
        if "budget_month" in changes and isinstance(changes["budget_month"], date):
            request.budget_month = changes["budget_month"].replace(day=1)
        if "needed_on" in changes:
            request.needed_on = changes["needed_on"]  # type: ignore[assignment]
        request.route = (await self._with_threshold(route, Decimal(request.amount))).as_value()
        request.version += 1
        self._event(request, actor, "edited", None)
        await self._session.flush()
        await self._record(actor, request_id, request, "edit")
        return request

    # --- documents -------------------------------------------------------------------------------

    async def missing(self, request: FinanceRequest) -> list[DocumentKind]:
        need = Route.from_value(request.route).documents
        have = set(
            (
                await self._session.scalars(
                    select(FinanceDocument.kind).where(FinanceDocument.request_id == request.id)
                )
            ).all()
        )
        return [kind for kind in need if kind.value not in have]

    async def upload(
        self,
        *,
        actor: Actor,
        access: FinanceAccess,
        request_id: uuid.UUID,
        finance_request_id: uuid.UUID,
        kind: str,
        name: str,
        data: bytes,
    ) -> FinanceDocument:
        request = await self._visible(finance_request_id, actor, access)
        await self._may_handle_papers(request, actor, access)
        document_kind = _kind(kind)
        if not data or len(data) > MAX_FILE_BYTES:
            raise FinanceInvalidError("file_invalid", "Tệp rỗng hoặc lớn hơn 10 MB.")
        content_type = sniff(data) or _office(data, name)
        if content_type is None:
            raise FinanceInvalidError(
                "file_invalid",
                "Chỉ nhận PDF, ảnh (JPEG / PNG / WebP), Word (.docx), Excel (.xlsx).",
            )
        clean = (name or "chung-tu").strip().replace("/", "-")[:200] or "chung-tu"
        document = FinanceDocument(
            request_id=request.id,
            kind=document_kind.value,
            name=clean,
            content_type=content_type,
            size=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            storage="db",
            uploaded_by_user_id=actor.user_id,
        )
        folder = (await FinanceCodeService(self._session, self._audit).spend_settings())[
            "drive_folder_id"
        ]
        if folder and isinstance(self._drive, GoogleDriveClient):
            try:
                document.drive_file_id = await self._drive.upload_bytes(
                    folder, f"{request.code} - {clean}", content_type, data
                )
                document.storage = "drive"
            except Exception:  # Drive down: keep the file here rather than lose it.
                logger.exception("finance_document_drive_upload_failed")
        if document.storage == "db":
            document.data = data
        self._session.add(document)
        self._event(request, actor, "document", f"{DOCUMENT_LABELS[document_kind]}: {clean}")
        await self._session.flush()
        await self._record(actor, request_id, request, f"document:{document_kind.value}")
        return document

    async def paper(
        self,
        *,
        actor: Actor,
        access: FinanceAccess,
        request_id: uuid.UUID,
        finance_request_id: uuid.UUID,
        kind: str,
    ) -> FinanceDocument:
        """F5: "Kế toán tick đã nhận bản giấy"."""
        request = await self._visible(finance_request_id, actor, access)
        await self._may_handle_papers(request, actor, access, accountants_only=True)
        document_kind = _kind(kind)
        document = FinanceDocument(
            request_id=request.id,
            kind=document_kind.value,
            name="Đã nhận bản giấy",
            storage="paper",
            uploaded_by_user_id=actor.user_id,
        )
        self._session.add(document)
        self._event(request, actor, "paper", DOCUMENT_LABELS[document_kind])
        await self._session.flush()
        await self._record(actor, request_id, request, f"paper:{document_kind.value}")
        return document

    async def remove_document(
        self,
        *,
        actor: Actor,
        access: FinanceAccess,
        request_id: uuid.UUID,
        document_id: uuid.UUID,
    ) -> None:
        document = await self._session.get(FinanceDocument, document_id)
        if document is None:
            raise FinanceNotFoundError("finance_document_not_found", "Không tìm thấy chứng từ.")
        request = await self._visible(document.request_id, actor, access)
        if not (access.view_requests or document.uploaded_by_user_id == actor.user_id):
            raise FinanceForbiddenError("Chỉ người tải lên hoặc kế toán xoá được.")
        if (
            request.status == RequestStatus.PAID.value
            and document.kind == DocumentKind.PAYMENT_PROOF.value
        ):
            raise FinanceInvalidError(
                "document_locked", "Chứng từ chi của hồ sơ đã chi không xoá được."
            )
        self._event(request, actor, "document_removed", document.name)
        await self._session.delete(document)
        await self._session.flush()
        await self._record(actor, request_id, request, "document_removed")

    async def file(
        self, *, actor: Actor, access: FinanceAccess, document_id: uuid.UUID
    ) -> tuple[FinanceDocument, bytes]:
        document = await self._session.scalar(
            select(FinanceDocument)
            .options(undefer(FinanceDocument.data))
            .where(FinanceDocument.id == document_id)
        )
        if document is None or document.storage == "paper":
            raise FinanceNotFoundError("finance_document_not_found", "Không tìm thấy chứng từ.")
        await self._visible(document.request_id, actor, access)
        if document.storage == "drive" and document.drive_file_id:
            if not isinstance(self._drive, GoogleDriveClient):
                raise FinanceInvalidError("drive_unavailable", "Máy chủ chưa kết nối Google Drive.")
            return document, await self._drive.download_bytes(document.drive_file_id)
        return document, document.data or b""

    async def _may_handle_papers(
        self,
        request: FinanceRequest,
        actor: Actor,
        access: FinanceAccess,
        *,
        accountants_only: bool = False,
    ) -> None:
        if access.view_requests:
            return
        steps = await self._steps(request, every_round=True)
        acting = any(self._may_act(step, actor) for step in steps)
        if accountants_only:
            accountant = any(
                self._may_act(step, actor)
                and (
                    step.role.startswith("ACC_")
                    or step.role
                    in (
                        FinanceRole.CHIEF_ACCOUNTANT.value,
                        FinanceRole.CASHIER_VP.value,
                        FinanceRole.FUND_BRANCH.value,
                    )
                )
                for step in steps
            )
            if not accountant:
                raise FinanceForbiddenError("Chỉ kế toán / thủ quỹ tick đã nhận bản giấy.")
            return
        if actor.user_id != request.requester_id and not acting:
            raise FinanceForbiddenError("Bạn không thêm chứng từ cho hồ sơ này được.")

    # --- reading ---------------------------------------------------------------------------------

    async def _heads_of(self, actor: Actor) -> set[uuid.UUID]:
        if actor.user_id is None:
            return set()
        seats = (await load_seats(self._session, [actor.user_id])).get(actor.user_id, ())
        return {seat.department_id for seat in seats if seat.position is Position.HEAD}

    async def _visible(
        self, finance_request_id: uuid.UUID, actor: Actor, access: FinanceAccess
    ) -> FinanceRequest:
        request = await self._session.get(FinanceRequest, finance_request_id)
        if request is None or not await self._sees(request, actor, access):
            raise FinanceNotFoundError("finance_request_not_found", "Không tìm thấy hồ sơ chi.")
        return request

    async def _sees(self, request: FinanceRequest, actor: Actor, access: FinanceAccess) -> bool:
        if access.view_requests or actor.user_id == request.requester_id:
            return True
        if request.department_id is not None and request.department_id in await self._heads_of(
            actor
        ):
            return True
        found = await self._session.scalar(
            select(func.count()).where(
                FinanceRequestStep.request_id == request.id,
                or_(
                    FinanceRequestStep.approver_id == actor.user_id,
                    FinanceRequestStep.standin_id == actor.user_id,
                ),
            )
        )
        return bool(found)

    async def listing(
        self,
        *,
        actor: Actor,
        access: FinanceAccess,
        tab: str = "mine",
        status: str | None = None,
        q: str = "",
    ) -> list[FinanceRequest]:
        statement = select(FinanceRequest)
        if tab == "awaiting":
            ids = await self._awaiting_ids(actor)
            statement = statement.where(FinanceRequest.id.in_(ids or [uuid.uuid4()]))
        elif tab == "all":
            if access.view_requests:
                pass
            else:
                heads = await self._heads_of(actor)
                acted = select(FinanceRequestStep.request_id).where(
                    or_(
                        FinanceRequestStep.approver_id == actor.user_id,
                        FinanceRequestStep.standin_id == actor.user_id,
                    )
                )
                statement = statement.where(
                    or_(
                        FinanceRequest.requester_id == actor.user_id,
                        FinanceRequest.department_id.in_(heads or [uuid.uuid4()]),
                        FinanceRequest.id.in_(acted),
                    )
                )
        else:
            statement = statement.where(FinanceRequest.requester_id == actor.user_id)
        if status:
            statement = statement.where(FinanceRequest.status == status)
        text = q.strip()
        if text:
            statement = statement.where(
                or_(FinanceRequest.code.ilike(f"%{text}%"), FinanceRequest.title.ilike(f"%{text}%"))
            )
        return list(
            (
                await self._session.scalars(
                    statement.order_by(FinanceRequest.created_at.desc()).limit(500)
                )
            ).all()
        )

    async def _awaiting_ids(self, actor: Actor) -> list[uuid.UUID]:
        if actor.user_id is None:
            return []
        rows = await self._session.scalars(
            select(FinanceRequestStep.request_id)
            .join(FinanceRequest, FinanceRequest.id == FinanceRequestStep.request_id)
            .where(
                FinanceRequestStep.status == StepStatus.PENDING.value,
                FinanceRequest.status == RequestStatus.PENDING.value,
                or_(
                    FinanceRequestStep.approver_id == actor.user_id,
                    FinanceRequestStep.standin_id == actor.user_id,
                ),
            )
        )
        return list(dict.fromkeys(rows.all()))

    async def awaiting_count(self, actor: Actor) -> int:
        return len(await self._awaiting_ids(actor))

    async def detail(
        self, *, actor: Actor, access: FinanceAccess, finance_request_id: uuid.UUID
    ) -> RequestView:
        request = await self._visible(finance_request_id, actor, access)
        steps = await self._steps(request, every_round=True)
        if request.status == RequestStatus.PENDING.value:
            await self._refresh_open(
                request, [s for s in steps if s.round == max(x.round for x in steps)]
            )
        events = list(
            (
                await self._session.scalars(
                    select(FinanceRequestEvent)
                    .where(FinanceRequestEvent.request_id == request.id)
                    .order_by(FinanceRequestEvent.id)
                )
            ).all()
        )
        documents = list(
            (
                await self._session.scalars(
                    select(FinanceDocument)
                    .where(FinanceDocument.request_id == request.id)
                    .order_by(FinanceDocument.created_at)
                )
            ).all()
        )
        ids: set[uuid.UUID] = {request.requester_id}
        for step in steps:
            ids |= {x for x in (step.approver_id, step.standin_id, step.decided_by_user_id) if x}
        ids |= {event.actor_id for event in events if event.actor_id}
        ids |= {doc.uploaded_by_user_id for doc in documents if doc.uploaded_by_user_id}
        names = {
            user.id: user.full_name or ""
            for user in await self._session.scalars(select(User).where(User.id.in_(ids)))
        }
        open_step = (
            next((step for step in steps if step.status == StepStatus.PENDING.value), None)
            if request.status == RequestStatus.PENDING.value
            else None
        )
        can: list[str] = []
        if open_step is not None and self._may_act(open_step, actor):
            phase = Phase(open_step.phase)
            can += (
                ["approve", "return", "reject"]
                if phase in (Phase.PURCHASE, Phase.PAYMENT)
                else ["execute"]
                if phase is Phase.EXECUTE
                else ["pay"]
            )
        if actor.user_id == request.requester_id and request.status == RequestStatus.RETURNED.value:
            can += ["edit", "resubmit"]
        if request.status in (RequestStatus.PENDING.value, RequestStatus.RETURNED.value) and (
            actor.user_id == request.requester_id or actor.role in (Role.OWNER, Role.ADMIN)
        ):
            can.append("cancel")
        code = await self._session.get(FinanceCode, request.budget_code)
        entity = await self._session.get(LegalEntity, request.legal_entity_id)
        department = (
            await self._session.get(Department, request.department_id)
            if request.department_id
            else None
        )
        budget = await FinanceBudgetService(self._session, self._audit).check(
            month=request.budget_month,
            legal_entity_id=request.legal_entity_id,
            department_id=request.department_id,
            code=request.budget_code,
        )
        return RequestView(
            request=request,
            steps=steps,
            events=events,
            documents=documents,
            names=names,
            missing=await self.missing(request),
            budget=budget,
            can_act=can,
            open_step=open_step,
            code_name=code.name if code else request.budget_code,
            entity_name=f"{entity.code} - {entity.name}" if entity else "",
            department_name=department.name if department else "",
        )

    # --- history / audit / notices ---------------------------------------------------------------

    def _event(
        self, request: FinanceRequest, actor: Actor | None, action: str, note: str | None
    ) -> None:
        self._session.add(
            FinanceRequestEvent(
                request_id=request.id,
                actor_id=actor.user_id if actor else None,
                action=action,
                note=note,
            )
        )

    async def _record(
        self, actor: Actor, request_id: uuid.UUID, request: FinanceRequest, action: str
    ) -> None:
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.FINANCE_REQUEST_ACTION.value,
            result=AuditResult.SUCCESS,
            entity_type="finance_request",
            entity_id=str(request.id),
            after_data={
                "action": action,
                "code": request.code,
                "status": request.status,
                "phase": request.phase,
                "amount": str(request.amount),
            },
        )

    async def _notify_open(self, request: FinanceRequest, step: FinanceRequestStep) -> None:
        phase = Phase(step.phase)
        verb = {
            Phase.PURCHASE: "duyệt đề nghị mua sắm",
            Phase.PAYMENT: "duyệt thanh toán",
            Phase.EXECUTE: "thực hiện mua sắm & nghiệm thu",
            Phase.CASH: "chi tiền",
        }[phase]
        body = (
            f"{request.code}: {request.title} - {_vnd(request.amount)}. "
            f"Chờ bạn {verb} ({ROLE_LABELS[FinanceRole(step.role)]})."
        )
        await FinanceNotifier(self._session, self._settings).send(
            [person for person in (step.approver_id, step.standin_id) if person],
            event=NotificationEvent.FINANCE_REQUEST_AWAITING,
            heading="Có hồ sơ chi chờ bạn",
            body=body,
            request=request,
            key=f"open:{step.id}",
        )

    async def _notify_requester(self, request: FinanceRequest, heading: str, body: str) -> None:
        await FinanceNotifier(self._session, self._settings).send(
            [request.requester_id],
            event=NotificationEvent.FINANCE_REQUEST_UPDATED,
            heading=heading,
            body=f"{request.code}: {request.title}. {body}",
            request=request,
            key=f"update:{request.version}:{request.status}",
        )


class FinanceNotifier:
    """Inbox + a Telegram copy for a reachable private chat."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    async def send(
        self,
        recipients: Iterable[uuid.UUID],
        *,
        event: NotificationEvent,
        heading: str,
        body: str,
        request: FinanceRequest | None,
        key: str,
        path: str | None = None,
    ) -> None:
        inbox = UserNotificationService(self._session)
        router = NotificationRouter(self._session, self._settings)
        resolver = RecipientResolver(self._session, self._settings)
        target = path or (f"/finance/requests/{request.id}" if request else "/finance/requests")
        link = f"{self._settings.web_base_url.rstrip('/')}{target}"
        aggregate = request.id if request else uuid.uuid5(uuid.NAMESPACE_URL, key)
        for recipient in dict.fromkeys(recipients):
            idempotency = f"finance:{aggregate}:{key}:{event.value}:{recipient}"
            await inbox.record(
                recipient_user_id=recipient,
                event=event,
                title=heading,
                body=body,
                idempotency_key=idempotency,
                target_kind="finance_request",
                target_id=request.id if request else None,
            )
            resolved = await resolver.private_destination(user_id=recipient)
            if not resolved.is_resolved or resolved.telegram_chat_id is None:
                continue
            await router.route(
                [
                    RouteRequest(
                        event_type=event,
                        template_key="finance.request",
                        payload={
                            "heading": heading.upper(),
                            "title": request.title if request else heading,
                            "code": request.code if request else "Tài chính",
                            "body": body,
                            "link": link,
                        },
                        idempotency_key=idempotency,
                        aggregate_type="finance_request",
                        aggregate_id=aggregate,
                        recipient_user_id=recipient,
                        private_chat_id=resolved.telegram_chat_id,
                        created_by_user_id=None,
                        business_summary=f"Hồ sơ chi - {heading}",
                    )
                ]
            )


def _approver(step: FinanceRequestStep) -> uuid.UUID | None:
    return step.approver_id


def _kind(value: str) -> DocumentKind:
    try:
        return DocumentKind(value)
    except ValueError as error:
        raise FinanceInvalidError(
            "document_kind_invalid", "Loại chứng từ không có.", field="kind"
        ) from error


def _office(data: bytes, name: str) -> str | None:
    if not data.startswith(b"PK"):
        return None
    lowered = (name or "").lower()
    for content_type, suffix in OFFICE_TYPES.items():
        if lowered.endswith(suffix):
            return content_type
    return None


def _vnd(value: Decimal) -> str:
    return f"{int(value):,}".replace(",", ".") + " ₫"


def _who(actor: Actor) -> str:
    return actor.full_name or "Người duyệt"


__all__ = [
    "DOCS_GRACE_DAYS",
    "FinanceNotifier",
    "FinanceRequestMovedError",
    "FinanceRequestService",
    "RequestView",
]
