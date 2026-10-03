"""Who MeoBot is: one structured profile, one source of truth."""

from meobot.domain.assistant.profile import (
    ASSISTANT_PROFILE_SETTING_KEY,
    AssistantProfile,
    default_assistant_profile,
)

__all__ = [
    "ASSISTANT_PROFILE_SETTING_KEY",
    "AssistantProfile",
    "default_assistant_profile",
]
