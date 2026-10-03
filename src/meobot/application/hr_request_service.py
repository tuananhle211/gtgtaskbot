"""Creating, approving and amending leave and late-arrival requests.

Five rules are enforced here rather than left to a handler, because each one is
a way the feature could quietly go wrong:

* **You cannot file for somebody else, and you cannot approve your own.** Both
  are checked against the authoritative :class:`~meobot.domain.identity.models.Actor`,
  never against anything a message said.
* **Approval is OWNER-only in this release.** The legacy ``hr.approve_leave``
  permission still exists and still belongs to TEAM_LEAD, but it is not what
  gates this flow - :attr:`~meobot.domain.permissions.matrix.Permission.HR_REQUEST_APPROVE`
  is, and only the owner holds it. Widening that is a deliberate future
  decision, not something this release should do by reusing a permission that
  happened to be lying around.
* **Every mutation bumps ``version``.** Approval buttons are signed against the
  version they were rendered for, so a request amended after the card was sent
  cannot be approved on the strength of the old card.
* **Deciding twice is idempotent.** A second press of Approve on an
  already-approved request returns it unchanged rather than writing a second
  decision.
* **Nothing is ever deleted.** Withdrawal and cancellation are status changes
  with an event row; the history stays.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.core.errors import AuthorizationError, ConflictError, NotFoundError, ValidationError
from meobot.core.logging import get_logger
from meobot.core.time import ensure_utc, utcnow
from meobot.db.models.hr import HrRequest, HrRequestEvent
from meobot.db.models.user import User
from meobot.domain.access.models import UserStatus
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.hr.models import (
    HrEventType,
    HrRequestStatus,
    HrRequestType,
    overlaps,
)
from meobot.domain.hr.schedule import WorkSchedule, leave_interval
from meobot.domain.identity.labels import role_label
from meobot.domain.identity.models import Actor, Role
from meobot.domain.permissions.matrix import Permission, has_permission

logger = get_logger(__name__)

#: Refusals, in the words the person will read.
CANNOT_APPROVE_OWN = "Bạn không thể tự duyệt yêu cầu của mình."
NOT_YOUR_REQUEST = "Đây không phải yêu cầu của bạn."
ONLY_OWNER_APPROVES = f"Chỉ {role_label(Role.OWNER)} được duyệt yêu cầu nghỉ phép và đi muộn."
ALREADY_DECIDED = "Yêu cầu này đã được xử lý rồi."
PAST_DATE = "Bạn chỉ có thể xin nghỉ hoặc đi muộn cho hôm nay và những ngày sắp tới."
OVERLAPPING = "Bạn đã có một yêu cầu khác trùng với khoảng thời gian này."
DUPLICATE_LATE = "Bạn đã có yêu cầu đi muộn cho ngày này rồi."
INVALID_RANGE = "Giờ kết thúc phải sau giờ bắt đầu."
ACCOUNT_BLOCKED = "Tài khoản của bạn hiện chưa thể gửi yêu cầu. Vui lòng liên hệ Trưởng phòng."


class HrRequestService:
    """The lifecycle of one leave or late-arrival request.

    Args:
        session: Unit of work. The caller owns the transaction boundary.
        audit: Audit writer sharing that session.
    """

    def __init__(self, session: AsyncSession, audit: AuditService) -> None:
        self._session = session
        self._audit = audit

    # --- Creation ---------------------------------------------------------
    async def submit(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        request_type: HrRequestType,
        work_date: date,
        schedule: WorkSchedule,
        end_date: date | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        expected_arrival_at: datetime | None = None,
        late_minutes: int | None = None,
        reason: str = "",
        allow_past: bool = False,
        source: str = "text",
    ) -> HrRequest:
        """File a request for the acting person, already validated.

        The caller has collected every field conversationally and shown a
        preview; this is the point of no return, so every rule is re-checked
        here rather than trusted from the flow.

        Raises:
            AuthorizationError: The account may not file requests.
            ValidationError: A date, range or duplicate rule was broken.
        """
        user = await self._require_active_user(actor)

        today = datetime.now(tz=schedule.zone).date()
        if work_date < today and not allow_past:
            raise ValidationError(PAST_DATE)

        if start_at is None or end_at is None:
            local_start, local_end = leave_interval(
                request_type, day=work_date, schedule=schedule, end_day=end_date
            )
            start_at = local_start.astimezone(UTC)
            end_at = local_end.astimezone(UTC)
        if end_at <= start_at:
            raise ValidationError(INVALID_RANGE)

        await self._assert_no_conflict(
            user_id=user.id,
            request_type=request_type,
            work_date=work_date,
            start_at=start_at,
            end_at=end_at,
        )

        now = utcnow()
        row = HrRequest(
            requester_user_id=user.id,
            request_type=request_type,
            status=HrRequestStatus.PENDING,
            start_at=start_at,
            end_at=end_at,
            work_date=work_date,
            end_date=end_date,
            expected_arrival_at=expected_arrival_at,
            late_minutes=late_minutes,
            reason=(reason or "").strip()[:500] or None,
            version=1,
            submitted_at=now,
        )
        self._session.add(row)
        await self._session.flush()

        await self._record_event(
            row,
            event_type=HrEventType.SUBMITTED,
            actor=actor,
            before=None,
            after=HrRequestStatus.PENDING,
            metadata={"source": source, "request_type": request_type.value},
        )
        await self._audit_change(
            request_id=request_id,
            actor=actor,
            row=row,
            action=AuditAction.HR_REQUEST_SUBMITTED.value,
            before=None,
            source=source,
        )
        logger.info(
            "hr_request_submitted",
            extra={"hr_request_id": str(row.id), "request_type": request_type.value},
        )
        return row

    async def _require_active_user(self, actor: Actor) -> User:
        """The ``users`` row behind the actor, refusing blocked accounts."""
        if actor.user_id is None:
            raise AuthorizationError(ACCOUNT_BLOCKED)
        user = await self._session.get(User, actor.user_id)
        if user is None:
            raise AuthorizationError(ACCOUNT_BLOCKED)
        if not user.may_use_meobot or user.status is not UserStatus.ACTIVE:
            raise AuthorizationError(ACCOUNT_BLOCKED)
        if not has_permission(actor.role, Permission.HR_REQUEST_LEAVE):
            raise AuthorizationError(ACCOUNT_BLOCKED)
        return user

    async def _assert_no_conflict(
        self,
        *,
        user_id: uuid.UUID,
        request_type: HrRequestType,
        work_date: date,
        start_at: datetime,
        end_at: datetime,
    ) -> None:
        """Refuse a request that clashes with one this person already has.

        Only open and approved requests count: a rejected or withdrawn request
        is not a claim on anybody's time.
        """
        result = await self._session.execute(
            select(HrRequest).where(
                HrRequest.requester_user_id == user_id,
                HrRequest.status.in_(
                    [
                        HrRequestStatus.PENDING,
                        HrRequestStatus.APPROVED,
                        HrRequestStatus.CHANGE_REQUESTED,
                    ]
                ),
            )
        )
        for existing in result.scalars().all():
            if (
                request_type is HrRequestType.LATE_ARRIVAL
                and existing.request_type is HrRequestType.LATE_ARRIVAL
                and existing.work_date == work_date
            ):
                raise ValidationError(DUPLICATE_LATE)
            if existing.start_at is None or existing.end_at is None:
                continue
            if overlaps(
                ensure_utc(existing.start_at), ensure_utc(existing.end_at), start_at, end_at
            ):
                raise ValidationError(OVERLAPPING)

    # --- Decisions --------------------------------------------------------
    async def approve(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        hr_request_id: uuid.UUID,
        expected_version: int | None = None,
        note: str | None = None,
    ) -> HrRequest:
        """Approve a pending request. Idempotent."""
        return await self._decide(
            actor=actor,
            request_id=request_id,
            hr_request_id=hr_request_id,
            expected_version=expected_version,
            status=HrRequestStatus.APPROVED,
            event=HrEventType.APPROVED,
            action=AuditAction.HR_REQUEST_APPROVED.value,
            reason=None,
            note=note,
        )

    async def reject(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        hr_request_id: uuid.UUID,
        expected_version: int | None = None,
        reason: str | None = None,
    ) -> HrRequest:
        """Refuse a pending request, optionally saying why. Idempotent."""
        return await self._decide(
            actor=actor,
            request_id=request_id,
            hr_request_id=hr_request_id,
            expected_version=expected_version,
            status=HrRequestStatus.REJECTED,
            event=HrEventType.REJECTED,
            action=AuditAction.HR_REQUEST_REJECTED.value,
            reason=reason,
            note=None,
        )

    async def request_changes(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        hr_request_id: uuid.UUID,
        expected_version: int | None = None,
        note: str | None = None,
    ) -> HrRequest:
        """Send a request back for more information."""
        return await self._decide(
            actor=actor,
            request_id=request_id,
            hr_request_id=hr_request_id,
            expected_version=expected_version,
            status=HrRequestStatus.CHANGE_REQUESTED,
            event=HrEventType.CHANGE_REQUESTED,
            action=AuditAction.HR_REQUEST_CHANGE_REQUESTED.value,
            reason=note,
            note=None,
        )

    async def _decide(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        hr_request_id: uuid.UUID,
        expected_version: int | None,
        status: HrRequestStatus,
        event: HrEventType,
        action: str,
        reason: str | None,
        note: str | None,
    ) -> HrRequest:
        """Shared decision path: authorise, check version, write once."""
        if not has_permission(actor.role, Permission.HR_REQUEST_APPROVE):
            raise AuthorizationError(ONLY_OWNER_APPROVES)

        row = await self.require(hr_request_id)
        if row.requester_user_id == actor.user_id:
            raise AuthorizationError(CANNOT_APPROVE_OWN)
        if expected_version is not None and row.version != expected_version:
            raise ConflictError(
                "Yêu cầu này vừa được cập nhật. Bạn mở lại để xem nội dung mới nhé."
            )
        if row.status is status:
            # Same decision, pressed twice. Nothing to do and nothing to log.
            return row
        if row.status.is_final:
            raise ConflictError(ALREADY_DECIDED)

        before = row.status
        row.status = status
        row.decided_at = utcnow()
        row.decided_by_user_id = actor.user_id
        row.decision_reason = (reason or "").strip()[:500] or None
        if note:
            row.private_note = note.strip()[:1000]
        row.version += 1
        await self._session.flush()

        await self._record_event(
            row, event_type=event, actor=actor, before=before, after=status, metadata={}
        )
        await self._audit_change(
            request_id=request_id,
            actor=actor,
            row=row,
            action=action,
            before=before,
            source="button",
        )
        logger.info(
            "hr_request_decided",
            extra={"hr_request_id": str(row.id), "status": status.value},
        )
        return row

    # --- Requester-side changes ------------------------------------------
    async def withdraw(
        self, *, actor: Actor, request_id: uuid.UUID, hr_request_id: uuid.UUID
    ) -> HrRequest:
        """Take back a request that has not been decided yet.

        After approval this is refused: an approved absence the department has
        planned around cannot vanish silently. The Member files a cancellation
        instead, which the owner sees.
        """
        row = await self.require(hr_request_id)
        if row.requester_user_id != actor.user_id:
            raise AuthorizationError(NOT_YOUR_REQUEST)
        if row.status is HrRequestStatus.WITHDRAWN:
            return row
        if row.status is not HrRequestStatus.PENDING and row.status is not (
            HrRequestStatus.CHANGE_REQUESTED
        ):
            raise ConflictError(
                "Yêu cầu này đã được xử lý nên không rút lại được. "
                "Bạn hãy báo Trưởng phòng nếu cần thay đổi."
            )

        before = row.status
        row.status = HrRequestStatus.WITHDRAWN
        row.withdrawn_at = utcnow()
        row.version += 1
        await self._session.flush()
        await self._record_event(
            row,
            event_type=HrEventType.WITHDRAWN,
            actor=actor,
            before=before,
            after=HrRequestStatus.WITHDRAWN,
            metadata={},
        )
        await self._audit_change(
            request_id=request_id,
            actor=actor,
            row=row,
            action=AuditAction.HR_REQUEST_WITHDRAWN.value,
            before=before,
            source="text",
        )
        return row

    async def amend(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        hr_request_id: uuid.UUID,
        schedule: WorkSchedule,
        reason: str | None = None,
        expected_arrival_at: datetime | None = None,
        late_minutes: int | None = None,
    ) -> HrRequest:
        """Change a request that is still pending.

        Approved requests are deliberately out of reach: amending one silently
        would change a fact somebody has already acted on.
        """
        row = await self.require(hr_request_id)
        if row.requester_user_id != actor.user_id:
            raise AuthorizationError(NOT_YOUR_REQUEST)
        if row.status not in {HrRequestStatus.PENDING, HrRequestStatus.CHANGE_REQUESTED}:
            raise ConflictError(
                "Yêu cầu này đã được xử lý. Bạn hãy gửi yêu cầu mới nếu cần thay đổi."
            )

        before = row.status
        if reason is not None:
            row.reason = reason.strip()[:500] or None
        if expected_arrival_at is not None:
            row.expected_arrival_at = expected_arrival_at
        if late_minutes is not None:
            row.late_minutes = late_minutes
        row.status = HrRequestStatus.PENDING
        row.version += 1
        await self._session.flush()

        await self._record_event(
            row,
            event_type=HrEventType.AMENDED,
            actor=actor,
            before=before,
            after=HrRequestStatus.PENDING,
            metadata={},
        )
        await self._audit_change(
            request_id=request_id,
            actor=actor,
            row=row,
            action=AuditAction.HR_REQUEST_AMENDED.value,
            before=before,
            source="text",
        )
        return row

    # --- Reading ----------------------------------------------------------
    async def require(self, hr_request_id: uuid.UUID) -> HrRequest:
        """Load a request or raise the Vietnamese not-found error."""
        row = await self._session.get(HrRequest, hr_request_id)
        if row is None:
            raise NotFoundError("Mình không còn tìm thấy yêu cầu này.")
        return row

    async def for_requester(
        self, *, user_id: uuid.UUID, limit: int = 20, open_only: bool = False
    ) -> Sequence[HrRequest]:
        """One person's own requests, newest first.

        Scope is enforced here in the query, not by a caller remembering to
        filter - which is why there is no "all requests" variant reachable
        without the management permission.
        """
        statement = select(HrRequest).where(HrRequest.requester_user_id == user_id)
        if open_only:
            statement = statement.where(
                HrRequest.status.in_([HrRequestStatus.PENDING, HrRequestStatus.CHANGE_REQUESTED])
            )
        statement = statement.order_by(HrRequest.created_at.desc()).limit(limit)
        result = await self._session.execute(statement)
        return result.scalars().all()

    async def pending_for_approver(self, *, actor: Actor, limit: int = 20) -> Sequence[HrRequest]:
        """Everything waiting for a decision. Management-only."""
        if not has_permission(actor.role, Permission.HR_REQUEST_APPROVE):
            raise AuthorizationError(ONLY_OWNER_APPROVES)
        result = await self._session.execute(
            select(HrRequest)
            .where(HrRequest.status == HrRequestStatus.PENDING)
            .order_by(HrRequest.submitted_at.asc())
            .limit(limit)
        )
        return result.scalars().all()

    async def latest_open_for(self, *, user_id: uuid.UUID) -> HrRequest | None:
        """The single request "huỷ đơn vừa gửi" should mean, if unambiguous."""
        rows = await self.for_requester(user_id=user_id, limit=2, open_only=True)
        return rows[0] if len(rows) == 1 else None

    async def history(self, hr_request_id: uuid.UUID) -> Sequence[HrRequestEvent]:
        """Append-only history of one request."""
        result = await self._session.execute(
            select(HrRequestEvent)
            .where(HrRequestEvent.request_id == hr_request_id)
            .order_by(HrRequestEvent.created_at.asc())
        )
        return result.scalars().all()

    # --- Bookkeeping ------------------------------------------------------
    async def _record_event(
        self,
        row: HrRequest,
        *,
        event_type: HrEventType,
        actor: Actor,
        before: HrRequestStatus | None,
        after: HrRequestStatus | None,
        metadata: dict[str, object],
    ) -> None:
        """Append one history line. Never carries the reason or a private note."""
        self._session.add(
            HrRequestEvent(
                request_id=row.id,
                event_type=event_type,
                actor_user_id=actor.user_id,
                actor_telegram_id=actor.telegram_user_id,
                state_before=before.value if before else None,
                state_after=after.value if after else None,
                event_metadata=dict(metadata),
                created_at=utcnow(),
            )
        )
        await self._session.flush()

    async def _audit_change(
        self,
        *,
        request_id: uuid.UUID,
        actor: Actor,
        row: HrRequest,
        action: str,
        before: HrRequestStatus | None,
        source: str,
    ) -> None:
        """One audit row per mutation.

        Identifiers and state transitions only. The reason a person gave for
        needing time off is private and stays out of the audit payload.
        """
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=action,
            result=AuditResult.SUCCESS,
            entity_type="hr_request",
            entity_id=str(row.id),
            before_data={"status": before.value} if before else None,
            after_data={
                "status": row.status.value,
                "request_type": row.request_type.value,
                "work_date": row.work_date.isoformat(),
                "version": row.version,
                "requester_user_id": str(row.requester_user_id),
                "actor_role": actor.role.value,
                "actor_role_label": role_label(actor.role),
                "input_source": source,
            },
        )
