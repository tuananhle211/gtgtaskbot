"""Finding out a destination is broken before somebody's message needs it.

Until 0.6.0a2, health was learned only from real delivery failures. That means
the first person to discover MeoBot had been removed from a group was whoever's
announcement did not arrive - and they discovered it after the fact, about
something that mattered.

This probes instead. ``getChat`` and ``getChatMember`` **read** state; they do
not produce a message. No test message is ever sent into a real group full of
real colleagues, because a test message is not a health check, it is a
notification nobody asked for.

Two rules keep the probing from being worse than the problem:

**A transient error is not a verdict.** Telegram being briefly unreachable says
nothing about whether a group exists. Only a definite answer counts, and even
then an unhealthy verdict must repeat ``NOTIFICATION_HEALTH_FAILURE_THRESHOLD``
times before the destination is believed to be broken.

**One alert per transition, not per check.** ``health_version`` is bumped only
when the status actually changes, and it is part of the alert's idempotency
key. A group that has been broken for a week produces one alert, not one every
half hour.

Private chats are never probed. Telegram offers no way to ask "would this
person receive a message" that does not involve sending one, so private
reachability stays learned from real deliveries - which is what the ``users``
columns from 0.6.0a1 already record.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.core.config import Settings
from meobot.core.logging import get_logger
from meobot.core.time import ensure_utc, utcnow
from meobot.db.models.notifications import TelegramChat
from meobot.domain.notifications.models import DestinationHealth
from meobot.integrations.telegram.notifier import ChatProbe, HealthProbe

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class HealthTransition:
    """A destination whose health actually changed.

    Only these produce an alert. A check that confirms what was already known
    produces nothing.
    """

    chat: TelegramChat
    previous: DestinationHealth
    current: DestinationHealth
    health_version: int

    @property
    def became_healthy(self) -> bool:
        return self.current.is_healthy and not self.previous.is_healthy

    @property
    def became_unhealthy(self) -> bool:
        return not self.current.is_healthy and self.previous.is_healthy


def verdict_of(probe: ChatProbe) -> DestinationHealth:
    """Turn one probe into a health status."""
    if probe.provider_error:
        return DestinationHealth.PROVIDER_ERROR
    if not probe.exists:
        return DestinationHealth.NOT_FOUND
    if not probe.bot_is_member:
        return DestinationHealth.BOT_REMOVED
    if not probe.can_send:
        return DestinationHealth.CANNOT_SEND
    return DestinationHealth.HEALTHY


class DestinationHealthService:
    """Probes registered destinations and records what changed.

    Args:
        session: Unit of work. The caller owns the transaction boundary.
        settings: Batch size and the consecutive-failure threshold.
        probe: Any :class:`~meobot.integrations.telegram.notifier.HealthProbe`.
            Tests pass a fake; the worker passes the real client.
    """

    def __init__(self, session: AsyncSession, settings: Settings, probe: HealthProbe) -> None:
        self._session = session
        self._settings = settings
        self._probe = probe

    async def due_batch(self, *, now: datetime | None = None) -> Sequence[TelegramChat]:
        """Registered destinations that have not been checked recently.

        Bounded, and ordered oldest-first, so a deployment with many groups
        spreads its probes across several sweeps instead of making one burst of
        API calls that Telegram will rate-limit.
        """
        moment = now or utcnow()
        cutoff = moment - timedelta(
            seconds=self._settings.notification_destination_health_interval_seconds
        )
        result = await self._session.execute(
            select(TelegramChat)
            .where(
                TelegramChat.is_active.is_(True),
                or_(
                    TelegramChat.last_verified_at.is_(None),
                    TelegramChat.last_verified_at < cutoff,
                ),
            )
            .order_by(TelegramChat.last_verified_at.asc().nulls_first())
            .limit(self._settings.notification_destination_health_batch_size)
        )
        return result.scalars().all()

    async def check(
        self, chat: TelegramChat, *, now: datetime | None = None
    ) -> HealthTransition | None:
        """Probe one destination and record the result.

        Returns:
            The transition, when the status actually changed. ``None`` when the
            check confirmed what was already known - which is the normal case,
            and is why a broken group does not produce an alert every sweep.
        """
        moment = now or utcnow()
        previous = chat.health_status
        probe = await self._probe.probe_chat(chat.telegram_chat_id)
        verdict = verdict_of(probe)

        chat.last_verified_at = moment
        if probe.title:
            chat.telegram_title = probe.title[:300]

        if verdict is DestinationHealth.PROVIDER_ERROR:
            # Telegram did not answer. That is a fact about Telegram, not about
            # this group: nothing is disabled and no transition is reported.
            chat.last_health_error_category = verdict.value
            await self._session.flush()
            return None

        if verdict.is_healthy:
            return await self._record_healthy(chat, previous, probe, now=moment)
        return await self._record_unhealthy(chat, previous, verdict, now=moment)

    async def _record_healthy(
        self, chat: TelegramChat, previous: DestinationHealth, probe: ChatProbe, *, now: datetime
    ) -> HealthTransition | None:
        chat.consecutive_health_failures = 0
        chat.bot_can_send = True
        chat.bot_is_admin = probe.is_admin
        chat.last_healthy_at = now
        chat.last_health_error_category = None
        if previous is DestinationHealth.HEALTHY:
            await self._session.flush()
            return None

        chat.health_status = DestinationHealth.HEALTHY
        chat.health_version += 1
        # A destination that was disabled by a delivery failure comes back on
        # its own once it works again - a human should not have to remember to
        # re-enable a group after re-adding the bot to it.
        chat.is_active = True
        await self._session.flush()
        logger.info(
            "destination_recovered",
            extra={"chat_row_id": str(chat.id), "previous": previous.value},
        )
        return HealthTransition(
            chat=chat,
            previous=previous,
            current=DestinationHealth.HEALTHY,
            health_version=chat.health_version,
        )

    async def _record_unhealthy(
        self,
        chat: TelegramChat,
        previous: DestinationHealth,
        verdict: DestinationHealth,
        *,
        now: datetime,
    ) -> HealthTransition | None:
        chat.consecutive_health_failures += 1
        chat.last_health_error_category = verdict.value
        chat.last_unhealthy_at = now

        if chat.consecutive_health_failures < self._settings.notification_health_failure_threshold:
            # One bad answer is not enough. Telegram returns transient 400s, and
            # disabling a working group because of one would be worse than
            # noticing a real problem a sweep later.
            await self._session.flush()
            return None

        chat.bot_can_send = False
        if previous is verdict:
            await self._session.flush()
            return None

        chat.health_status = verdict
        chat.health_version += 1
        await self._session.flush()
        logger.warning(
            "destination_unhealthy",
            extra={"chat_row_id": str(chat.id), "verdict": verdict.value},
        )
        return HealthTransition(
            chat=chat, previous=previous, current=verdict, health_version=chat.health_version
        )

    @staticmethod
    def stale_since(chat: TelegramChat, *, now: datetime) -> timedelta | None:
        """How long since this destination was last verified."""
        if chat.last_verified_at is None:
            return None
        return now - ensure_utc(chat.last_verified_at)
