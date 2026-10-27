""" "🔄 Chạy lại" under a failed-job alert (0077): queue the job again now.

Only the OWNER may (the alert goes to the OWNER only, chốt 27/10); the same
rule and the same "already running" guard as the button on the web screen,
because both go through :class:`CronMonitorService`.
"""

from __future__ import annotations

import uuid

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from meobot.application.cron.monitor_service import (
    CronJobNotFoundError,
    CronJobRunningError,
    CronMonitorService,
)
from meobot.application.cron.schedule import beat_jobs
from meobot.bot.cron_keyboards import RERUN_ACTION, rerun_binding
from meobot.core.config import Settings
from meobot.core.logging import get_logger
from meobot.core.time import utcnow
from meobot.db.session import Database
from meobot.domain.identity.models import Actor, Role
from meobot.domain.member.callbacks import data_pattern, parse

logger = get_logger(__name__)

router = Router(name="cron")

QUEUED = "Đã đưa tác vụ vào hàng đợi chạy lại. Kết quả sẽ được báo nếu vẫn lỗi."
RUNNING = "Tác vụ đang chạy, đợi lượt này xong."
STALE = "Nút này không còn hiệu lực."
NOT_PERMITTED = "Chỉ chủ sở hữu mới chạy lại được tác vụ."


@router.callback_query(F.data.regexp(data_pattern((RERUN_ACTION,))))
async def handle_rerun(
    query: CallbackQuery,
    database: Database,
    settings: Settings,
    actor: Actor | None = None,
) -> None:
    chat_id = query.message.chat.id if isinstance(query.message, Message) else 0
    payload = parse(
        query.data or "", secret=settings.callback_secret, binding=rerun_binding(chat_id)
    )
    if payload is None or payload.entity_id is None or payload.is_expired(utcnow()):
        await query.answer(STALE, show_alert=True)
        return
    if actor is None or actor.role is not Role.OWNER:
        await query.answer(NOT_PERMITTED, show_alert=True)
        return
    jobs = {job.key: job for job in beat_jobs()}
    try:
        async with database.transaction() as session:
            service = CronMonitorService(session, settings, jobs=jobs)
            key = await service.incident_job(payload.entity_id)
            if key is None:
                await query.answer(STALE, show_alert=True)
                return
            await service.rerun(
                actor=actor, request_id=uuid.uuid4(), key=key, incident_id=payload.entity_id
            )
    except CronJobRunningError:
        await query.answer(RUNNING, show_alert=True)
        return
    except CronJobNotFoundError:
        await query.answer(STALE, show_alert=True)
        return
    await query.answer(QUEUED, show_alert=True)


__all__ = ["router"]
