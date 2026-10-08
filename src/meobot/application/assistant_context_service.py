"""The one place that answers "who is MeoBot".

Storage strategy, and why there is no new table: an assistant profile is a
single mutable document that an operator changes rarely. ``system_settings``
already exists for exactly that, it already versions writes, and it already
records who made them. Adding an ``assistant_profiles`` table would have given
us a second thing to migrate for no behaviour we do not already have.

Reading is therefore three-layered and never fails:

1. the ``system_settings`` row, when an operator has customised the profile;
2. otherwise the defaults, filled in from configuration
   (``MEOBOT_ORGANIZATION_NAME`` and friends);
3. and if the database is unreachable, still (2) - a chat turn must not die
   because a settings lookup did.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.core.config import Settings
from meobot.core.logging import get_logger
from meobot.db.models.system_setting import SystemSetting
from meobot.domain.assistant.profile import (
    ASSISTANT_PROFILE_SETTING_KEY,
    AssistantProfile,
    default_assistant_profile,
)

logger = get_logger(__name__)

#: Fields an operator may override through ``system_settings``. Anything else
#: in the stored document is ignored rather than trusted.
_OVERRIDABLE: frozenset[str] = frozenset(
    {
        "assistant_name",
        "identity",
        "mission",
        "organization_name",
        "department_name",
        "department_size",
        "operating_domains",
        "core_responsibilities",
        "tone",
        "response_preferences",
    }
)

#: Sequence-valued fields, coerced back into tuples on read.
_SEQUENCE_FIELDS: frozenset[str] = frozenset(
    {"operating_domains", "core_responsibilities", "tone", "response_preferences"}
)


class AssistantContextService:
    """Loads (and optionally stores) MeoBot's own profile.

    Args:
        settings: Supplies the configured organisation and department.
        session: Optional. Without one the configured defaults are returned -
            which is what the API's stateless paths and the tests use.
    """

    def __init__(self, settings: Settings, session: AsyncSession | None = None) -> None:
        self._settings = settings
        self._session = session

    def defaults(self) -> AssistantProfile:
        """The profile MeoBot ships with, filled in from configuration."""
        return default_assistant_profile(
            organization_name=self._settings.meobot_organization_name,
            department_name=self._settings.meobot_department_name,
            department_size=self._settings.meobot_department_size,
            assistant_name=self._settings.app_name or "TasksBot",
        )

    async def load(self) -> AssistantProfile:
        """The effective profile: stored overrides on top of the defaults.

        Never raises. A storage failure degrades the assistant's self-
        description; it must not stop it answering.
        """
        profile = self.defaults()
        if self._session is None:
            return profile
        try:
            result = await self._session.execute(
                select(SystemSetting).where(SystemSetting.key == ASSISTANT_PROFILE_SETTING_KEY)
            )
            row = result.scalar_one_or_none()
        except Exception:
            logger.warning("assistant_profile_load_failed")
            return profile
        if row is None or not isinstance(row.value, dict):
            return profile
        return self._apply(profile, row.value)

    @staticmethod
    def _apply(profile: AssistantProfile, stored: dict[str, Any]) -> AssistantProfile:
        """Merge a stored document onto the defaults, field by field.

        Unknown keys are dropped rather than passed through: the document is
        operator input, and an unvalidated field would become prompt text.
        """
        updates: dict[str, Any] = {}
        for key, value in stored.items():
            if key not in _OVERRIDABLE or value in (None, "", []):
                continue
            if key in _SEQUENCE_FIELDS:
                if isinstance(value, list):
                    updates[key] = tuple(str(item)[:200] for item in value[:30])
                continue
            if isinstance(value, str):
                updates[key] = value[:1000]
        if not updates:
            return profile
        try:
            return profile.model_copy(update=updates)
        except ValueError:  # pragma: no cover - defensive against bad stored data
            logger.warning("assistant_profile_invalid_override")
            return profile

    async def save_overrides(self, overrides: dict[str, Any], *, updated_by: Any = None) -> None:
        """Persist operator overrides, bumping the setting's version.

        Only OWNER/ADMIN reach this - the permission check lives at the command
        boundary, where the actor is known.

        Raises:
            RuntimeError: When the service was built without a session.
        """
        if self._session is None:
            raise RuntimeError("AssistantContextService needs a session to save overrides")
        clean = {key: value for key, value in overrides.items() if key in _OVERRIDABLE}
        result = await self._session.execute(
            select(SystemSetting).where(SystemSetting.key == ASSISTANT_PROFILE_SETTING_KEY)
        )
        row = result.scalar_one_or_none()
        if row is None:
            self._session.add(
                SystemSetting(
                    key=ASSISTANT_PROFILE_SETTING_KEY,
                    value=clean,
                    description="Hồ sơ danh tính của TasksBot (do người vận hành chỉnh).",
                    version=1,
                    updated_by=updated_by,
                )
            )
        else:
            merged = dict(row.value or {})
            merged.update(clean)
            row.value = merged
            row.version += 1
            row.updated_by = updated_by
        await self._session.flush()
        logger.info("assistant_profile_updated", extra={"fields": sorted(clean)})
