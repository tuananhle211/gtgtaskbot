"""Which Telegram groups MeoBot is allowed to send to, and what they are for.

**Registration is deliberate, and happens in the group.** The bot receiving a
message somewhere does not register it: a bot added to a customer group would
otherwise become a valid destination for internal announcements. Somebody with
authority has to say "đăng ký đây là group Content" *in that group*, and
confirm it.

**Identity is the numeric chat id.** A group title is something any admin can
change; routing that broke on a rename, or worse, followed a renamed group,
would be a bad trade. The title is stored for display and refreshed
opportunistically, and is never how a row is found.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.core.errors import AuthorizationError, ConflictError, ValidationError
from meobot.core.logging import get_logger
from meobot.core.time import utcnow
from meobot.db.models.notifications import TelegramChat
from meobot.domain.identity.labels import role_label
from meobot.domain.identity.models import Actor, Role
from meobot.domain.member.normalization import strip_accents
from meobot.domain.notifications.models import (
    ChatPurpose,
    DestinationHealth,
    FailureCategory,
    PrivacyClassification,
)
from meobot.domain.permissions.matrix import Permission, has_permission

logger = get_logger(__name__)

ONLY_MANAGERS_REGISTER = (
    f"Chỉ {role_label(Role.OWNER)} và {role_label(Role.ADMIN)} được đăng ký group."
)
NOT_IN_A_GROUP = (
    "Lệnh này chỉ dùng được **trong chính group** bạn muốn đăng ký.\n"
    "Bạn mở group đó rồi nhắn lại giúp mình nhé."
)
UNKNOWN_PURPOSE = "Mình chưa rõ group này dùng để làm gì."


def normalize_alias(name: str) -> str:
    """Fold a group name for matching: accent-free, lower case, one space."""
    return " ".join(strip_accents(name or "").split())


def _lines(stored: str | None) -> tuple[str, ...]:
    """One newline-separated column as a tuple, blanks and duplicates removed."""
    seen: list[str] = []
    for raw in (stored or "").splitlines():
        value = raw.strip()
        if value and value not in seen:
            seen.append(value)
    return tuple(seen)


def _store_lines(values: Sequence[str] | None) -> str | None:
    """The inverse of :func:`_lines`. ``None`` rather than "" when empty."""
    cleaned = _lines("\n".join(values or ()))
    return "\n".join(cleaned) or None


def aliases_of(row: TelegramChat) -> tuple[str, ...]:
    """Extra names one group answers to, in the words somebody entered.

    Added in 0.6.0a3. Before it, a destination was reachable by its display
    name and its Telegram title, so "Saykeng - Vựa Idea" had to be typed in
    full - and people say "group idea".
    """
    return _lines(row.aliases_text)


def tags_of(row: TelegramChat) -> tuple[str, ...]:
    """Free labels on one group: "brainstorm", "nội dung", "sáng tạo".

    Tags are what make "các group content" a *set* rather than a name, and they
    are matched exactly the same way a name is: folded, whole-word, against
    something somebody actually stored.
    """
    return _lines(row.tags_text)


class ChatRegistryService:
    """Creates, reads and disables registered Telegram destinations.

    Args:
        session: Unit of work. The caller owns the transaction boundary.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- Reading ----------------------------------------------------------
    async def by_telegram_id(
        self, *, bot_identity: int, telegram_chat_id: int
    ) -> TelegramChat | None:
        """The registration for one Telegram chat, active or not."""
        result = await self._session.execute(
            select(TelegramChat).where(
                TelegramChat.bot_identity == bot_identity,
                TelegramChat.telegram_chat_id == telegram_chat_id,
            )
        )
        return result.scalar_one_or_none()

    async def by_id(self, chat_id: uuid.UUID) -> TelegramChat | None:
        """One registration by primary key."""
        return await self._session.get(TelegramChat, chat_id)

    async def active(self, *, bot_identity: int) -> Sequence[TelegramChat]:
        """Every destination currently usable, newest first.

        The second sort key is not decoration. This order *is* the numbering a
        person reads off the card, and "hai group đầu" counts against it - so a
        tie on ``created_at``, which two registrations a second apart really can
        produce, must not be allowed to select different groups on two
        consecutive requests.
        """
        result = await self._session.execute(
            select(TelegramChat)
            .where(
                TelegramChat.bot_identity == bot_identity,
                TelegramChat.is_active.is_(True),
            )
            .order_by(TelegramChat.created_at.desc(), TelegramChat.telegram_chat_id.desc())
        )
        return result.scalars().all()

    async def all_registered(self, *, bot_identity: int) -> Sequence[TelegramChat]:
        """Everything ever registered, including disabled rows."""
        result = await self._session.execute(
            select(TelegramChat)
            .where(TelegramChat.bot_identity == bot_identity)
            .order_by(TelegramChat.created_at.desc())
        )
        return result.scalars().all()

    async def usable(self, *, bot_identity: int) -> list[TelegramChat]:
        """Active destinations MeoBot is currently able to send to.

        "Able" is four separate facts, and conflating them is how a group the
        bot was removed from stayed on the menu: registration is active,
        automated delivery is allowed, the bot could send last time it tried,
        and the health probe has not found it permanently broken.

        ``PROVIDER_ERROR`` deliberately does not exclude a group: Telegram
        being briefly unreachable says nothing about whether the group exists,
        and treating it as evidence would empty the list during any outage.
        """
        rows = await self.active(bot_identity=bot_identity)
        return [
            row
            for row in rows
            if row.allow_automated_delivery
            and row.bot_can_send
            and row.health_status
            not in {
                DestinationHealth.BOT_REMOVED,
                DestinationHealth.NOT_FOUND,
            }
        ]

    async def visible_to(self, *, actor: Actor, bot_identity: int) -> list[TelegramChat]:
        """Every destination this person is allowed to see and address.

        Not a display filter. A Trưởng nhóm asking "có những group nào?" is
        told about the groups they manage and no others, because a list is
        itself information: knowing that a "Ban giám đốc" group exists is
        something an assignment does not grant.
        """
        rows = await self.active(bot_identity=bot_identity)
        if has_permission(actor.role, Permission.ANNOUNCEMENT_BROADCAST) or has_permission(
            actor.role, Permission.SETTINGS_WRITE
        ):
            return list(rows)
        if actor.user_id is None:
            return []
        from meobot.application.chat_assignment_service import ChatAssignmentService

        managed = await ChatAssignmentService(self._session).managed_chats(user_id=actor.user_id)
        allowed = {row.id for row in managed}
        return [row for row in rows if row.id in allowed]

    async def by_purpose(
        self, *, bot_identity: int, purpose: ChatPurpose
    ) -> Sequence[TelegramChat]:
        """Active destinations registered for one purpose."""
        result = await self._session.execute(
            select(TelegramChat).where(
                TelegramChat.bot_identity == bot_identity,
                TelegramChat.purpose == purpose,
                TelegramChat.is_active.is_(True),
            )
        )
        return result.scalars().all()

    # --- Writing ----------------------------------------------------------
    async def register(
        self,
        *,
        actor: Actor,
        bot_identity: int,
        telegram_chat_id: int,
        chat_type: str,
        telegram_title: str | None,
        display_name: str,
        purpose: ChatPurpose,
        privacy_level: PrivacyClassification = PrivacyClassification.PUBLIC_OPERATIONAL,
        allow_automated_delivery: bool = True,
        department: str | None = None,
        team: str | None = None,
        aliases: Sequence[str] | None = None,
        tags: Sequence[str] | None = None,
        brand: str | None = None,
    ) -> TelegramChat:
        """Register, or update, one group. Idempotent.

        Registering an already-registered group updates it in place and bumps
        ``version`` rather than creating a second row - so a confirmation
        pressed twice ends with one destination, not two.

        Raises:
            AuthorizationError: The actor may not register destinations.
            ValidationError: Called from somewhere that is not a group.
        """
        self._require_manager(actor)
        if chat_type not in {"group", "supergroup", "channel"}:
            # A private chat claiming to be a group is exactly the shape an
            # attempt to register a destination out of band would take.
            raise ValidationError(NOT_IN_A_GROUP)

        now = utcnow()
        existing = await self.by_telegram_id(
            bot_identity=bot_identity, telegram_chat_id=telegram_chat_id
        )
        if existing is not None:
            existing.display_name = display_name[:200]
            existing.normalized_alias = normalize_alias(display_name)
            existing.telegram_title = (telegram_title or None) and telegram_title[:300]
            existing.purpose = purpose
            existing.privacy_level = privacy_level
            existing.allow_automated_delivery = allow_automated_delivery
            existing.department = department
            existing.team = team
            existing.chat_type = chat_type
            existing.is_active = True
            existing.registered_by_user_id = actor.user_id
            existing.registered_at = now
            # Re-registering without naming aliases keeps the ones already
            # there. Somebody saying "đăng ký lại group này" is correcting a
            # name, not asking to forget every alias they added afterwards.
            if aliases is not None:
                existing.aliases_text = _store_lines(aliases)
            if tags is not None:
                existing.tags_text = _store_lines(tags)
            if brand is not None:
                existing.brand = brand[:200] or None
            existing.version += 1
            await self._session.flush()
            logger.info("telegram_chat_reregistered", extra={"chat_id": telegram_chat_id})
            return existing

        row = TelegramChat(
            telegram_chat_id=telegram_chat_id,
            bot_identity=bot_identity,
            chat_type=chat_type,
            telegram_title=(telegram_title or None) and telegram_title[:300],
            display_name=display_name[:200],
            normalized_alias=normalize_alias(display_name),
            department=department,
            team=team,
            purpose=purpose,
            privacy_level=privacy_level,
            allow_automated_delivery=allow_automated_delivery,
            bot_can_send=True,
            is_active=True,
            registered_by_user_id=actor.user_id,
            registered_at=now,
            version=1,
            aliases_text=_store_lines(aliases),
            tags_text=_store_lines(tags),
            brand=(brand or None) and brand[:200],
        )
        self._session.add(row)
        await self._session.flush()
        logger.info(
            "telegram_chat_registered",
            extra={"chat_id": telegram_chat_id, "purpose": purpose.value},
        )
        return row

    async def set_active(self, *, actor: Actor, chat_id: uuid.UUID, active: bool) -> TelegramChat:
        """Turn a destination on or off. The row is kept either way."""
        self._require_manager(actor)
        row = await self.by_id(chat_id)
        if row is None:
            raise ConflictError("Không tìm thấy group này trong danh sách đã đăng ký.")
        row.is_active = active
        row.version += 1
        await self._session.flush()
        return row

    async def set_metadata(
        self,
        *,
        actor: Actor,
        chat_id: uuid.UUID,
        aliases: Sequence[str] | None = None,
        tags: Sequence[str] | None = None,
        brand: str | None = None,
        department: str | None = None,
        team: str | None = None,
    ) -> TelegramChat:
        """Add the names and labels people actually use for one group.

        Everything is optional and ``None`` means "leave alone", so setting a
        brand does not silently clear the aliases somebody added last week.

        Raises:
            AuthorizationError: The actor may not change registrations.
            ConflictError: No such registration.
        """
        self._require_manager(actor)
        row = await self.by_id(chat_id)
        if row is None:
            raise ConflictError("Không tìm thấy group này trong danh sách đã đăng ký.")
        if aliases is not None:
            row.aliases_text = _store_lines(aliases)
        if tags is not None:
            row.tags_text = _store_lines(tags)
        if brand is not None:
            row.brand = brand[:200] or None
        if department is not None:
            row.department = department[:200] or None
        if team is not None:
            row.team = team[:200] or None
        row.version += 1
        await self._session.flush()
        return row

    async def mark_used(self, chat_id: uuid.UUID) -> None:
        """Record that something was just sent here.

        Only ever used to order the registry list and to answer "group vừa
        rồi". It has no bearing on where anything goes.
        """
        row = await self.by_id(chat_id)
        if row is not None:
            row.last_used_at = utcnow()
            await self._session.flush()

    async def refresh_title(self, row: TelegramChat, title: str | None) -> None:
        """Keep the display title current without changing identity."""
        if title and title[:300] != row.telegram_title:
            row.telegram_title = title[:300]
            await self._session.flush()

    async def record_health(
        self, *, chat_id: uuid.UUID, category: FailureCategory
    ) -> TelegramChat | None:
        """Learn from a delivery failure that the destination is unusable.

        Called by the worker. A group the bot has been removed from stops being
        offered as a destination instead of collecting failed messages.
        """
        row = await self.by_id(chat_id)
        if row is None:
            return None
        if category in {FailureCategory.BOT_NOT_IN_CHAT, FailureCategory.CHAT_NOT_FOUND}:
            row.bot_can_send = False
            row.is_active = False
        elif category is FailureCategory.BOT_CANNOT_SEND:
            row.bot_can_send = False
        elif category is FailureCategory.NONE:
            row.bot_can_send = True
            row.last_verified_at = utcnow()
        await self._session.flush()
        return row

    @staticmethod
    def _require_manager(actor: Actor) -> None:
        """Only OWNER and ADMIN register destinations.

        Deliberately reusing ``settings.write`` rather than inventing a
        permission: registering a destination is a configuration change, and
        that is the permission this codebase already uses for one.
        """
        if not has_permission(actor.role, Permission.SETTINGS_WRITE):
            raise AuthorizationError(ONLY_MANAGERS_REGISTER)
