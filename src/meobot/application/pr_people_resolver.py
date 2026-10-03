"""Turning "Linh" into one MeoBot user, or into a question.

**Never a similarity guess.** If two active people could plausibly be meant,
this returns them both and the caller asks. That is the rule
:class:`~meobot.application.recipient_resolver.RecipientResolver` already
applies to chats, and it matters more here: assigning a task to the wrong Linh
is quiet, and stays quiet until a deadline passes.

Why this lives in the application layer
---------------------------------------

Telegram is client #1 and a web admin UI is client #2. "Who is Linh" is a
question both will ask, and an answer computed inside a Telegram handler would
have to be written twice - differently, eventually. So it sits beside the PR
services, takes no transport type, and returns domain values.

It resolves against ``users`` and nothing else. A Telegram username is not
consulted: usernames change, they are not unique across time, and MeoBot
already treats them as display data rather than identity.

What it matches
---------------

In order, and it stops at the first tier that produces any hit at all:

1. the whole name, accent-folded - "nguyen thi linh";
2. any single word of the name as a whole word - "linh", "nguyen";
3. a substring - "ngu".

Tier order is what makes "Linh" find the person called Linh rather than
everybody whose name contains those four letters. Within a tier, two hits is
two hits: it asks.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.db.models.user import User
from meobot.domain.access.models import UserStatus
from meobot.domain.member.normalization import strip_accents

#: How many candidates are worth showing before the phrase is simply too vague
#: to be a question anybody can answer.
MAX_CANDIDATES = 8


@dataclass(frozen=True, slots=True)
class PersonMatch:
    """One person a phrase could mean."""

    user_id: uuid.UUID
    full_name: str


@dataclass(frozen=True, slots=True)
class PersonResolution:
    """One person, several, or none - and never an opinion about which.

    Three states rather than an optional user, because "nobody by that name"
    and "four people by that name" need different sentences from the caller.
    """

    match: PersonMatch | None = None
    candidates: tuple[PersonMatch, ...] = ()

    @property
    def resolved(self) -> bool:
        return self.match is not None

    @property
    def ambiguous(self) -> bool:
        return self.match is None and bool(self.candidates)

    @property
    def missing(self) -> bool:
        return self.match is None and not self.candidates


class PrPeopleResolver:
    """Finds the ``users`` row a person's name refers to.

    Args:
        session: A session. Nothing here writes - resolving a name must never
            be able to create a person, which is the failure mode that turns a
            typo into a permanent second account.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def resolve(self, name: str) -> PersonResolution:
        """One person named ``name``, the candidates, or nothing."""
        needle = strip_accents((name or "").strip())
        if not needle:
            return PersonResolution()

        people = await self._active_people()
        for tier in (self._whole_name, self._whole_word, self._substring):
            hits = [person for person in people if tier(person, needle)]
            if len(hits) == 1:
                return PersonResolution(match=self._match(hits[0]))
            if hits:
                return PersonResolution(
                    candidates=tuple(self._match(person) for person in hits[:MAX_CANDIDATES])
                )
        return PersonResolution()

    async def by_id(self, user_id: uuid.UUID) -> PersonMatch | None:
        """The person behind an id, for rendering a name back to a human."""
        user = await self._session.get(User, user_id)
        return self._match(user) if user is not None else None

    # --- Internals --------------------------------------------------------
    async def _active_people(self) -> Sequence[User]:
        """Everybody who could be given work.

        Only ``ACTIVE`` accounts. Assigning a task to a suspended person, or
        granting them a review right, records a commitment nobody can act on -
        and hiding them here means the caller reports "no such person" rather
        than resolving to somebody who cannot help.
        """
        result = await self._session.execute(
            select(User).where(User.status == UserStatus.ACTIVE).order_by(User.full_name.asc())
        )
        return result.scalars().all()

    @staticmethod
    def _match(user: User) -> PersonMatch:
        return PersonMatch(user_id=user.id, full_name=user.full_name)

    @staticmethod
    def _whole_name(user: User, needle: str) -> bool:
        return strip_accents(user.full_name or "") == needle

    @staticmethod
    def _whole_word(user: User, needle: str) -> bool:
        return needle in strip_accents(user.full_name or "").split()

    @staticmethod
    def _substring(user: User, needle: str) -> bool:
        return needle in strip_accents(user.full_name or "")


__all__: list[str] = ["MAX_CANDIDATES", "PersonMatch", "PersonResolution", "PrPeopleResolver"]
