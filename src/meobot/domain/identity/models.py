"""Actor and role vocabulary."""

from __future__ import annotations

import uuid
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Role(StrEnum):
    """Coarse-grained role. Fine-grained rights live in ``domain.permissions``."""

    OWNER = "OWNER"
    ADMIN = "ADMIN"
    TEAM_LEAD = "TEAM_LEAD"
    EMPLOYEE = "EMPLOYEE"

    @property
    def rank(self) -> int:
        """Higher rank == broader authority. Used for 'at least' comparisons."""
        return _ROLE_RANK[self]

    def outranks(self, other: Role) -> bool:
        """True when this role is strictly more privileged than ``other``."""
        return self.rank > other.rank


_ROLE_RANK: dict[Role, int] = {
    Role.EMPLOYEE: 10,
    Role.TEAM_LEAD: 20,
    Role.ADMIN: 30,
    Role.OWNER: 40,
}


class Actor(BaseModel):
    """The authenticated principal behind a request.

    This is a domain value object; it is built from the ``users`` table (or,
    for bootstrap, from ``MEOBOT_OWNER_TELEGRAM_ID``) at the transport boundary.
    """

    model_config = ConfigDict(frozen=True)

    user_id: uuid.UUID | None = Field(
        default=None,
        description="Row id in `users`. None for the bootstrap owner before first sync.",
    )
    telegram_user_id: int | None = None
    telegram_username: str | None = None
    full_name: str = "unknown"
    role: Role = Role.EMPLOYEE
    active: bool = True
    is_bootstrap_owner: bool = Field(
        default=False,
        description="True when identity came from MEOBOT_OWNER_TELEGRAM_ID, not the database.",
    )

    @property
    def display_name(self) -> str:
        if self.telegram_username:
            return f"{self.full_name} (@{self.telegram_username})"
        return self.full_name

    @property
    def is_guest(self) -> bool:
        """True when no ``users`` row backs this actor.

        Somebody who has been let in temporarily but is not a member of the
        system. It changes what MeoBot *calls* them - see
        :func:`~meobot.domain.identity.labels.actor_label` - and nothing else:
        authority still comes from :attr:`role` and the permission matrix.
        The bootstrap owner is excluded; they are configuration, not a guest.
        """
        return self.user_id is None and not self.is_bootstrap_owner

    def describe(self) -> str:
        """Short, log-safe description of the actor.

        Deliberately the raw enum: logs and audit records are read by operators
        and must line up with the database, so display labels stay out of them.
        """
        return f"{self.full_name}[{self.role.value}]"
