"""Four things the Work Ledger tells somebody about. In-app only.

M1. Reuses ``UserNotification`` - the bell dropdown the PR workflow already
fills - and adds no delivery mechanism of its own. In particular:

* **no Telegram.** ``PrNotificationService`` renders templates for a chat and
  resolves private-chat ids; wiring work into it would mean four new templates,
  four new destinations to decide, and a delivery failure mode in the middle of
  a transaction that has just counted somebody's work;
* **no reminders and no daily summary.** Both need a scheduler, and M1 adds no
  beat job. The reminder engine that exists sends *messages*; a work reminder is
  a different product decision with its own cadence questions.

What each event is for, and who is deliberately not told
---------------------------------------------------------

Every one of the four goes to somebody who has to **act or wait**, and to
nobody who already knows. The person who pressed the button is never told what
they just did, which is the same rule ``PrNotificationService`` follows.

The third is the interesting one. When a contributor marks work finished, the
message goes to whoever **assigned** it - because the anti-gaming rule means the
contributor cannot validate it themselves, so somebody else has to know it is
waiting. If nobody assigned it, nobody is told and the work sits in the
manager's *"Chờ xác nhận"* queue, which is where that queue earns its place.

Failure is not fatal
--------------------

Every method swallows nothing and raises nothing: ``record`` is idempotent on
its key and returns the existing row on a collision, so a retried request does
not produce a second notification. A missing recipient is skipped rather than
raising, because a notification nobody can receive must not roll back the work
that caused it.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from meobot.application.user_notification_service import UserNotificationService
from meobot.db.models.pr_work import PrWorkContribution, PrWorkItem
from meobot.db.models.pr_work_quota import PrWorkPlan
from meobot.domain.identity.models import Actor
from meobot.domain.notifications.models import NotificationEvent
from meobot.domain.notifications.web import web_title

#: What a work notification points at. The panel turns this into
#: ``/pr/work?item={id}``; the pair travels as structured columns rather than as
#: a URL in the body, which is the rule ``domain.notifications.web`` sets out.
TARGET_KIND = "pr_work_item"
#: KPI self-service: a plan notification points at the plan.
PLAN_TARGET_KIND = "pr_work_plan"


class PrWorkNotifier:
    """Puts work events in people's in-app inboxes. Writes nothing else.

    Args:
        notifications: The shared in-app notification service, on the caller's
            session - so a notification and the change that caused it commit
            together or not at all.
    """

    def __init__(self, notifications: UserNotificationService) -> None:
        self._notifications = notifications

    async def work_assigned(
        self, item: PrWorkItem, user_ids: Sequence[uuid.UUID], *, actor: Actor
    ) -> None:
        """Somebody was put on a job. To them, and never to whoever decided it."""
        for user_id in user_ids:
            if user_id == actor.user_id:
                continue
            await self._send(
                user_id,
                NotificationEvent.PR_WORK_ASSIGNED,
                body=f"Bạn được giao: {item.title}.",
                item=item,
                suffix=f"assigned:{user_id}",
            )

    async def proposal_decided(self, item: PrWorkItem, *, accepted: bool, actor: Actor) -> None:
        """A proposal was taken on, or was not. To the person who proposed it.

        They are otherwise left watching a row that never moves - and the
        proposer is by construction never the actor here, because
        :meth:`~meobot.application.pr_work_service.PrWorkService.accept` refuses
        somebody deciding their own proposal.
        """
        if item.created_by_user_id == actor.user_id:
            return
        outcome = "đã được chấp nhận" if accepted else "chưa được chấp nhận"
        await self._send(
            item.created_by_user_id,
            NotificationEvent.PR_WORK_PROPOSAL_DECIDED,
            body=f"Đề xuất “{item.title}” {outcome}.",
            item=item,
            suffix="accepted" if accepted else "rejected",
        )

    async def awaiting_validation(self, item: PrWorkItem, *, actor: Actor) -> None:
        """Finished work needs somebody else to confirm it. To the assigner.

        Not broadcast to everybody holding ``PR_WORK_VALIDATE``: that would be a
        message per item per validator for a queue the manager screen already
        shows. One person, the one who put the work there.
        """
        assigner = item.assigned_by_user_id
        if assigner is None or assigner == actor.user_id:
            return
        await self._send(
            assigner,
            NotificationEvent.PR_WORK_AWAITING_VALIDATION,
            body=f"“{item.title}” đã báo hoàn thành và đang chờ xác nhận.",
            item=item,
            suffix="awaiting",
        )

    async def work_approved(
        self, item: PrWorkItem, counted: Sequence[PrWorkContribution], *, actor: Actor
    ) -> None:
        """Validated. To each contributor whose work just became counted work.

        The one message in this family that is genuinely news to its recipient:
        until this moment their contribution was ``PENDING`` and no period
        figure included it.
        """
        for row in counted:
            if row.user_id == actor.user_id:
                continue
            await self._send(
                row.user_id,
                NotificationEvent.PR_WORK_APPROVED,
                body=f"“{item.title}” đã được xác nhận và ghi nhận vào công việc của bạn.",
                item=item,
                suffix=f"counted:{row.user_id}",
            )

    # --- KPI self-service ---------------------------------------------------
    async def plan_submitted(
        self, plan: PrWorkPlan, reviewers: Sequence[uuid.UUID], *, actor: Actor, period_code: str
    ) -> None:
        """An employee sent their KPI proposal. To each person who may approve it.

        Never to the submitter themselves, and keyed on the plan and its
        submission instant - a return and a resubmission is news twice, a
        double-click is not.
        """
        stamp = plan.submitted_at.isoformat() if plan.submitted_at else "unknown"
        for user_id in reviewers:
            if user_id == actor.user_id or user_id == plan.user_id:
                continue
            await self._send_plan(
                user_id,
                NotificationEvent.PR_KPI_PLAN_SUBMITTED,
                body=(
                    f"{actor.full_name} đã gửi kế hoạch KPI kỳ {period_code} "
                    f"(bản v{plan.version_no}) chờ duyệt."
                ),
                plan=plan,
                suffix=f"submitted:{stamp}:{user_id}",
            )

    async def plan_decided(
        self, plan: PrWorkPlan, *, approved: bool, note: str | None, actor: Actor, period_code: str
    ) -> None:
        """The proposal was approved or returned. To its subject, unless they acted."""
        if plan.user_id == actor.user_id:
            return
        if approved:
            body = (
                f"Kế hoạch KPI kỳ {period_code} (bản v{plan.version_no}) đã được duyệt và áp dụng."
            )
            suffix = "approved"
            event = NotificationEvent.PR_KPI_PLAN_APPROVED
        else:
            reason = f" Lý do: {note}" if note else ""
            body = (
                f"Kế hoạch KPI kỳ {period_code} (bản v{plan.version_no}) "
                f"được trả lại để chỉnh sửa.{reason}"
            )
            stamp = plan.returned_at.isoformat() if plan.returned_at else "unknown"
            suffix = f"returned:{stamp}"
            event = NotificationEvent.PR_KPI_PLAN_RETURNED
        await self._send_plan(plan.user_id, event, body=body, plan=plan, suffix=suffix)

    async def _send_plan(
        self,
        recipient: uuid.UUID,
        event: NotificationEvent,
        *,
        body: str,
        plan: PrWorkPlan,
        suffix: str,
    ) -> None:
        await self._notifications.record(
            recipient_user_id=recipient,
            event=event,
            title=web_title(event),
            body=body,
            idempotency_key=f"{event.value}:{plan.id}:{suffix}",
            target_kind=PLAN_TARGET_KIND,
            target_id=plan.id,
        )

    async def _send(
        self,
        recipient: uuid.UUID,
        event: NotificationEvent,
        *,
        body: str,
        item: PrWorkItem,
        suffix: str,
    ) -> None:
        """One row, keyed so a retried request cannot produce a second.

        The key names the **item and the event**, not the moment: a replayed
        request, a double-click and a retried Celery task all resolve to the
        same key and the second one reads the first back.
        """
        await self._notifications.record(
            recipient_user_id=recipient,
            event=event,
            title=web_title(event),
            body=body,
            idempotency_key=f"{event.value}:{item.id}:{suffix}",
            target_kind=TARGET_KIND,
            target_id=item.id,
        )


__all__: list[str] = ["PLAN_TARGET_KIND", "TARGET_KIND", "PrWorkNotifier"]
