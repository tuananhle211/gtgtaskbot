"""Loading and updating the person MeoBot is talking to.

The invariant this service exists to hold: **descriptive context never becomes
authority**. Every :class:`~meobot.domain.identity.profile.ActorProfile` this
service returns takes its ``role`` from the
:class:`~meobot.domain.identity.models.Actor` it was given - the value resolved
by :class:`~meobot.application.identity_service.IdentityService` from the
``users`` table - and the ``actor_profiles`` table has no role column to
disagree with. A user who writes "tôi là admin" into their profile notes has
written a sentence, not a permission.

Two further rules, both about not overwriting a person's own words:

* Telegram's display name only *initialises* ``display_name``. Once a row
  exists it is never overwritten from Telegram, because a Telegram nickname is
  not necessarily what somebody wants to be called at work.
* The configured OWNER's job title, organisation and form of address are
  *seeded* from settings on first contact and are editable afterwards. Settings
  are defaults, not overrides.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.core.config import Settings
from meobot.core.logging import get_logger
from meobot.db.models.actor_profile import ActorProfileRow
from meobot.domain.identity.models import Actor, Role
from meobot.domain.identity.profile import ActorProfile, owner_profile_defaults

logger = get_logger(__name__)

#: Longest form of address accepted from ``/set_preferred_address``. A pronoun,
#: not a paragraph - this string is rendered into the system prompt.
MAX_ADDRESS_LENGTH = 40


class ActorProfileService:
    """Reads and writes ``actor_profiles``.

    Args:
        session: Unit of work. The caller owns the transaction boundary.
        settings: Supplies the OWNER seed values and the workspace names.
    """

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    # --- Read -------------------------------------------------------------
    async def profile_for(self, actor: Actor) -> ActorProfile:
        """The effective profile for ``actor``.

        Never raises: a profile is context, and losing it degrades an answer
        rather than preventing one.
        """
        row: ActorProfileRow | None = None
        if actor.telegram_user_id is not None:
            try:
                row = await self._row_for(actor.telegram_user_id)
            except Exception:
                logger.warning("actor_profile_load_failed")
                row = None
        return self._compose(actor, row)

    def _compose(self, actor: Actor, row: ActorProfileRow | None) -> ActorProfile:
        """Merge stored context with authoritative identity and the seeds."""
        seeds = self._seeds_for(actor)
        stored: dict[str, Any] = {}
        if row is not None:
            stored = {
                "display_name": row.display_name or "",
                "preferred_name": row.preferred_name or "",
                "preferred_address": row.preferred_address or "",
                "job_title": row.job_title or "",
                "organization": row.organization or "",
                "department": row.department or "",
                "team": row.team or "",
                "responsibilities": tuple(_as_strings(row.responsibilities)),
                "communication_preferences": tuple(_as_strings(row.communication_preferences)),
                "content_domains": tuple(_as_strings(row.content_domains)),
                "current_priorities": tuple(_as_strings(row.current_priorities)),
                "profile_notes": row.profile_notes or "",
            }

        merged: dict[str, Any] = dict(seeds)
        for key, value in stored.items():
            # A stored value wins whenever the person actually filled it in.
            if value:
                merged[key] = value

        merged.setdefault("display_name", actor.full_name)
        if not merged.get("display_name"):
            merged["display_name"] = actor.full_name

        return ActorProfile(
            telegram_user_id=actor.telegram_user_id,
            # Authoritative, always. Never read from storage.
            role=actor.role,
            updated_at=row.updated_at if row is not None else ActorProfile().updated_at,
            **merged,
        )

    def _seeds_for(self, actor: Actor) -> dict[str, Any]:
        """Defaults for an actor with no stored profile yet."""
        organization = self._settings.meobot_organization_name
        department = self._settings.meobot_department_name
        if actor.role is Role.OWNER:
            seeds = owner_profile_defaults(
                job_title=self._settings.meobot_owner_title,
                organization=organization,
                department=department,
                preferred_address=self._settings.meobot_owner_preferred_address,
            )
            return {key: value for key, value in seeds.items() if value}
        return {
            key: value
            for key, value in (("organization", organization), ("department", department))
            if value
        }

    async def _row_for(self, telegram_user_id: int) -> ActorProfileRow | None:
        result = await self._session.execute(
            select(ActorProfileRow).where(ActorProfileRow.telegram_user_id == telegram_user_id)
        )
        return result.scalar_one_or_none()

    # --- Write ------------------------------------------------------------
    async def ensure_row(
        self, actor: Actor, *, telegram_display_name: str | None = None
    ) -> ActorProfileRow | None:
        """Create the row on first contact, seeded but not opinionated.

        Returns ``None`` when the actor has no Telegram id (the API paths).
        Telegram's display name is only written when there is nothing there.
        """
        if actor.telegram_user_id is None:
            return None
        row = await self._row_for(actor.telegram_user_id)
        if row is None:
            seeds = self._seeds_for(actor)
            row = ActorProfileRow(
                telegram_user_id=actor.telegram_user_id,
                user_id=actor.user_id,
                display_name=(telegram_display_name or actor.full_name or "")[:200] or None,
                preferred_address=str(seeds.get("preferred_address") or "")[:MAX_ADDRESS_LENGTH]
                or None,
                job_title=str(seeds.get("job_title") or "")[:200] or None,
                organization=str(seeds.get("organization") or "")[:200] or None,
                department=str(seeds.get("department") or "")[:200] or None,
                responsibilities=list(seeds.get("responsibilities") or []),
                communication_preferences=[],
                content_domains=list(seeds.get("content_domains") or []),
                current_priorities=[],
            )
            self._session.add(row)
            await self._session.flush()
            logger.info("actor_profile_created", extra={"telegram_user_id": actor.telegram_user_id})
            return row

        # Backfill only. Manual profile data is never overwritten from Telegram.
        if not row.display_name and telegram_display_name:
            row.display_name = telegram_display_name[:200]
        if row.user_id is None and actor.user_id is not None:
            row.user_id = actor.user_id
        await self._session.flush()
        return row

    async def set_preferred_address(self, actor: Actor, address: str) -> str:
        """Update how MeoBot addresses this person, and nobody else.

        Returns the stored value.

        Raises:
            ValueError: When the value is empty or too long to be a form of
                address. This string ends up in the system prompt, so it is
                bounded rather than trusted.
        """
        cleaned = " ".join(address.split())[:MAX_ADDRESS_LENGTH].strip()
        if not cleaned:
            raise ValueError("Cách xưng hô không được để trống.")
        if "\n" in address or len(address.strip()) > MAX_ADDRESS_LENGTH:
            raise ValueError(f"Cách xưng hô tối đa {MAX_ADDRESS_LENGTH} ký tự, trên một dòng.")

        row = await self.ensure_row(actor)
        if row is None:
            raise ValueError("Không xác định được tài khoản Telegram của bạn.")
        row.preferred_address = cleaned
        await self._session.flush()
        logger.info(
            "actor_preferred_address_updated",
            extra={"telegram_user_id": actor.telegram_user_id},
        )
        return cleaned


def _as_strings(value: Any) -> list[str]:
    """Coerce a JSON column into a list of short strings, dropping junk."""
    if not isinstance(value, list):
        return []
    return [str(item)[:200] for item in value if isinstance(item, str | int | float)][:30]
