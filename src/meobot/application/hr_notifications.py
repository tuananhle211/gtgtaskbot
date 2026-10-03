"""Turning HR transitions into outbound messages, in the same transaction.

This is the join between the HR module (which already existed and is not
rewritten here) and the notification router. It is a separate module rather
than methods on ``HrRequestService`` for one reason: HR should not have to know
that Telegram exists, and this way it still does not.

**The reason never leaves a private chat.** The approval card carries it,
because the person deciding needs it. The attendance-group update is built from
a different template that has no ``reason`` field at all - so the neutral
version cannot accidentally include it, even if somebody passes it in.

Every function here is called with the **same session** as the HR transition it
follows, so the transition and the intent to announce it commit together.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.notification_router import (
    NotificationRouter,
    RouteRequest,
    RouteResult,
    hr_approved_key,
    hr_attendance_key,
    hr_more_info_key,
    hr_rejected_key,
    hr_submitted_key,
)
from meobot.application.recipient_resolver import RecipientResolver
from meobot.core.config import Settings
from meobot.core.logging import get_logger
from meobot.db.models.hr import HrRequest
from meobot.db.models.user import User
from meobot.domain.hr.models import HrRequestType, type_label
from meobot.domain.hr.schedule import (
    WorkSchedule,
    describe_period,
    format_local_date,
    format_local_datetime,
    format_local_time,
)
from meobot.domain.notifications.models import ChatPurpose, NotificationEvent

logger = get_logger(__name__)


def summarise(row: HrRequest) -> str:
    """A short, neutral description of one request.

    Used in messages to the requester, who already knows their own reason - so
    this deliberately contains no reason at all and is safe to reuse anywhere.
    """
    period = describe_period(row.request_type, day=row.work_date, end_day=row.end_date)
    if row.request_type is HrRequestType.LATE_ARRIVAL:
        return f"đi muộn ngày {format_local_date(row.work_date)}"
    return f"{type_label(row.request_type).lower()} — {period}"


class HrNotificationService:
    """Queues the messages one HR transition should produce.

    Args:
        session: The same unit of work as the HR change.
        settings: Supplies the attendance-group switch and the owner id.
    """

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._router = NotificationRouter(session, settings)
        self._resolver = RecipientResolver(session, settings)

    async def on_submitted(
        self, *, row: HrRequest, requester: User, schedule: WorkSchedule, source_chat_id: int
    ) -> RouteResult:
        """Tell the approver, privately, with the reason.

        This is a *system-generated* notification: the Member already confirmed
        the request that produced it, so it is not confirmed a second time.
        """
        owner = await self._resolver.owner()
        if not owner.is_resolved or owner.telegram_chat_id is None:
            logger.info("hr_owner_unreachable", extra={"hr_request_id": str(row.id)})
            return RouteResult()

        heading = (
            "⏰ YÊU CẦU ĐI MUỘN"
            if row.request_type is HrRequestType.LATE_ARRIVAL
            else "📩 YÊU CẦU NGHỈ PHÉP"
        )
        payload: dict[str, object] = {
            "heading": heading,
            "requester_name": requester.full_name,
            "period": describe_period(row.request_type, day=row.work_date, end_day=row.end_date),
            "submitted_at": format_local_datetime(row.submitted_at or row.created_at, schedule),
        }
        if row.reason:
            payload["reason"] = row.reason
        if row.expected_arrival_at is not None:
            payload["arrival"] = format_local_time(row.expected_arrival_at, schedule)
        if row.late_minutes is not None:
            payload["late_minutes"] = row.late_minutes

        return await self._router.route(
            [
                RouteRequest(
                    event_type=NotificationEvent.HR_REQUEST_SUBMITTED,
                    template_key="hr.request_to_approver",
                    payload=payload,
                    idempotency_key=hr_submitted_key(row.id, row.version, owner.telegram_chat_id),
                    aggregate_type="hr_request",
                    aggregate_id=row.id,
                    recipient_user_id=owner.user.id if owner.user else None,
                    private_chat_id=owner.telegram_chat_id,
                    source_chat_id=source_chat_id,
                    created_by_user_id=requester.id,
                )
            ]
        )

    async def on_decided(
        self,
        *,
        row: HrRequest,
        schedule: WorkSchedule,
        bot_identity: int,
        approved: bool,
        source_chat_id: int,
    ) -> RouteResult:
        """Tell the requester, and optionally the attendance group.

        Two messages with deliberately different content: the requester gets
        the decision and any reason for it; the group gets a date and a name.
        """
        requests: list[RouteRequest] = []
        private = await self._resolver.private_destination(user_id=row.requester_user_id)
        summary = summarise(row)

        if private.is_resolved and private.telegram_chat_id is not None:
            if approved:
                requests.append(
                    RouteRequest(
                        event_type=NotificationEvent.HR_REQUEST_APPROVED,
                        template_key="hr.approved_to_member",
                        payload={"summary": summary},
                        idempotency_key=hr_approved_key(row.id, row.version, row.requester_user_id),
                        aggregate_type="hr_request",
                        aggregate_id=row.id,
                        recipient_user_id=row.requester_user_id,
                        private_chat_id=private.telegram_chat_id,
                        source_chat_id=source_chat_id,
                    )
                )
            else:
                payload: dict[str, object] = {"summary": summary}
                if row.decision_reason:
                    payload["reason"] = row.decision_reason
                requests.append(
                    RouteRequest(
                        event_type=NotificationEvent.HR_REQUEST_REJECTED,
                        template_key="hr.rejected_to_member",
                        payload=payload,
                        idempotency_key=hr_rejected_key(row.id, row.version, row.requester_user_id),
                        aggregate_type="hr_request",
                        aggregate_id=row.id,
                        recipient_user_id=row.requester_user_id,
                        private_chat_id=private.telegram_chat_id,
                        source_chat_id=source_chat_id,
                    )
                )

        if approved and self._settings.notification_attendance_group_enabled:
            attendance = await self._attendance_request(
                row=row, schedule=schedule, bot_identity=bot_identity, source_chat_id=source_chat_id
            )
            if attendance is not None:
                requests.append(attendance)

        return await self._router.route(requests)

    async def _attendance_request(
        self,
        *,
        row: HrRequest,
        schedule: WorkSchedule,
        bot_identity: int,
        source_chat_id: int,
    ) -> RouteRequest | None:
        """The neutral group update, or ``None`` when there is nowhere to put it.

        Built from a template with no ``reason`` field. That is the enforcement:
        it is not that this function chooses to omit the reason, it is that the
        template would reject it.
        """
        resolution = await self._resolver.resolve_chat(
            bot_identity=bot_identity, purpose=ChatPurpose.ATTENDANCE
        )
        if not resolution.is_resolved or resolution.chat is None:
            return None

        person = await self._session.get(User, row.requester_user_id)
        name = person.full_name if person is not None else "Thành viên"

        if row.request_type is HrRequestType.LATE_ARRIVAL:
            if row.expected_arrival_at is None:
                return None
            return RouteRequest(
                event_type=NotificationEvent.HR_ATTENDANCE_UPDATE,
                template_key="hr.attendance_late",
                payload={
                    "person": name,
                    "arrival": format_local_time(row.expected_arrival_at, schedule),
                    "work_date": format_local_date(row.work_date),
                },
                idempotency_key=hr_attendance_key(
                    row.id, row.version, resolution.chat.telegram_chat_id
                ),
                aggregate_type="hr_request",
                aggregate_id=row.id,
                destination=resolution.chat,
                source_chat_id=source_chat_id,
            )

        period = describe_period(row.request_type, day=row.work_date, end_day=row.end_date).lower()
        return RouteRequest(
            event_type=NotificationEvent.HR_ATTENDANCE_UPDATE,
            template_key="hr.attendance_leave",
            payload={
                "person": name,
                "period": f"{type_label(row.request_type).lower()} {period}",
            },
            idempotency_key=hr_attendance_key(
                row.id, row.version, resolution.chat.telegram_chat_id
            ),
            aggregate_type="hr_request",
            aggregate_id=row.id,
            destination=resolution.chat,
            source_chat_id=source_chat_id,
        )

    async def on_more_info_requested(self, *, row: HrRequest, source_chat_id: int) -> RouteResult:
        """Ask the requester for more, privately."""
        private = await self._resolver.private_destination(user_id=row.requester_user_id)
        if not private.is_resolved or private.telegram_chat_id is None:
            return RouteResult()

        payload: dict[str, object] = {"summary": summarise(row)}
        if row.decision_reason:
            payload["note"] = row.decision_reason
        return await self._router.route(
            [
                RouteRequest(
                    event_type=NotificationEvent.HR_MORE_INFO_REQUESTED,
                    template_key="hr.more_info_to_member",
                    payload=payload,
                    idempotency_key=hr_more_info_key(row.id, row.version, row.requester_user_id),
                    aggregate_type="hr_request",
                    aggregate_id=row.id,
                    recipient_user_id=row.requester_user_id,
                    private_chat_id=private.telegram_chat_id,
                    source_chat_id=source_chat_id,
                )
            ]
        )


async def mark_private_chat_reachable(
    session: AsyncSession, *, telegram_user_id: int, chat_id: int
) -> None:
    """Record that this person's private chat works.

    Called when somebody messages the bot privately - which is the only way to
    learn it, since Telegram will not let a bot open a conversation. Without
    this, every private notification would be a guess.
    """
    from sqlalchemy import select

    from meobot.core.time import utcnow

    result = await session.execute(select(User).where(User.telegram_user_id == telegram_user_id))
    user = result.scalar_one_or_none()
    if user is None:
        return
    changed = not user.private_chat_available or user.telegram_private_chat_id != chat_id
    user.telegram_private_chat_id = chat_id
    user.private_chat_available = True
    user.last_private_interaction_at = utcnow()
    if changed:
        user.private_delivery_failure_category = None
        user.bot_blocked_at = None
