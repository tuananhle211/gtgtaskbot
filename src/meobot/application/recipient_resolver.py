"""Turning "gửi vào group Content" into one authoritative destination.

**Never a similarity guess.** If two registered groups could plausibly be meant,
this returns them both and the caller asks one question. A model picking the
"closest" name would be right most of the time, and the times it was wrong it
would post an internal announcement into the wrong team's chat - which is not a
failure mode worth trading for skipping one tap.

Resolution order, most specific first:

1. an exact registered alias;
2. an explicit chat purpose ("toàn phòng", "chấm công");
3. an explicit team assignment;
4. the destination already chosen in an active confirmed flow (the caller
   supplies it);
5. a unique normalized-name match;
6. otherwise: ask.

Private recipients are resolved from ``users``, never from a username. Telegram
will not let a bot open a conversation, so "can we reach this person privately"
is a stored fact learned from them pressing Start - see
:meth:`RecipientResolver.private_destination`.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.chat_registry_service import (
    ChatRegistryService,
    aliases_of,
    normalize_alias,
    tags_of,
)
from meobot.core.config import Settings
from meobot.core.logging import get_logger
from meobot.db.models.notifications import TelegramChat
from meobot.db.models.user import User
from meobot.domain.access.models import UserStatus
from meobot.domain.dispatch.models import SelectionSource
from meobot.domain.dispatch.phrases import AudienceScope, read_audience_scope, read_exclusions
from meobot.domain.identity.models import Actor
from meobot.domain.member.normalization import strip_accents
from meobot.domain.notifications.models import ChatPurpose, DestinationHealth
from meobot.domain.notifications.naming import GROUP_WORDS

logger = get_logger(__name__)

#: Words that name a purpose outright. Checked before any name matching.
PURPOSE_PHRASES: dict[str, ChatPurpose] = {
    "toan phong": ChatPurpose.DEPARTMENT_ANNOUNCEMENTS,
    "ca phong": ChatPurpose.DEPARTMENT_ANNOUNCEMENTS,
    "thong bao chung": ChatPurpose.DEPARTMENT_ANNOUNCEMENTS,
    "cham cong": ChatPurpose.ATTENDANCE,
    "bao cao": ChatPurpose.REPORTING,
    "quan ly": ChatPurpose.MANAGEMENT,
    "noi dung": ChatPurpose.CONTENT_TEAM,
    "content": ChatPurpose.CONTENT_TEAM,
    "san xuat": ChatPurpose.PRODUCTION_TEAM,
    "production": ChatPurpose.PRODUCTION_TEAM,
    "seeding": ChatPurpose.SEEDING_TEAM,
}

PRIVATE_UNAVAILABLE = (
    "TasksBot chưa thể nhắn riêng cho {name} vì {name} chưa bắt đầu cuộc trò chuyện với bot.\n\n"
    "Hãy nhờ {name} mở TasksBot và bấm ‘Bắt đầu’."
)

NO_DESTINATION = (
    "Mình chưa tìm thấy group nào phù hợp đã được đăng ký.\n"
    "Bạn vào group đó và nhắn “đăng ký đây là group …” giúp mình nhé."
)

ASK_WHICH_GROUP = "Bạn muốn gửi tới group nào?"


def _can_send(row: TelegramChat) -> bool:
    """Whether MeoBot could post here, as far as anybody currently knows."""
    return row.bot_can_send and row.health_status not in {
        DestinationHealth.BOT_REMOVED,
        DestinationHealth.NOT_FOUND,
        DestinationHealth.CANNOT_SEND,
    }


def _is_usable(row: TelegramChat) -> bool:
    """Active, allowed to receive automated messages, and reachable."""
    return row.is_active and row.allow_automated_delivery and _can_send(row)


def _unique(rows: Sequence[TelegramChat]) -> list[TelegramChat]:
    """De-duplicate destinations while keeping the order they were found in."""
    seen: set[uuid.UUID] = set()
    ordered: list[TelegramChat] = []
    for row in rows:
        if row.id not in seen:
            seen.add(row.id)
            ordered.append(row)
    return ordered


def alias_forms(row: TelegramChat) -> tuple[str, ...]:
    """Every folded name one registered destination answers to.

    A group registered as "Group Test" with Telegram title "Test" is named by
    people as *Test*, *group Test* and *nhóm Test*. All three mean the row, and
    none of them is a guess: each is derived from something that was actually
    stored for this destination.

    Since 0.6.0a3 that includes the aliases somebody entered for the group -
    which is what makes "Saykeng - Vựa Idea" reachable as "idea", the way a
    person would actually ask for it.
    """
    forms: set[str] = set()
    for raw in (row.normalized_alias, row.display_name, row.telegram_title, *aliases_of(row)):
        folded = normalize_alias(raw or "")
        if not folded:
            continue
        forms.add(folded)
        head, _, tail = folded.partition(" ")
        if head in GROUP_WORDS and tail:
            forms.add(tail)
    return tuple(sorted(forms, key=len, reverse=True))


def _matched_forms(row: TelegramChat, folded_text: str) -> tuple[str, ...]:
    """Which of this destination's names actually appear in the message."""
    return tuple(form for form in alias_forms(row) if _whole_word(form, folded_text))


def _whole_word(form: str, folded_text: str) -> bool:
    """Whole-word containment, so "Test" is not found inside "testing"."""
    if not form:
        return False
    return re.search(rf"(?<![\w]){re.escape(form)}(?![\w])", folded_text) is not None


def _label_forms(row: TelegramChat) -> tuple[str, ...]:
    """Folded labels that describe rather than name: tags, team, brand, area.

    Kept separate from :func:`alias_forms` on purpose. A name is what somebody
    *called* the group and a label is a property of it, and when a message
    contains both, the name wins - "gửi cho group Test" means Test, not every
    group tagged like Test.
    """
    forms: set[str] = set()
    for raw in (*tags_of(row), row.team, row.brand, row.department):
        folded = normalize_alias(raw or "")
        if folded:
            forms.add(folded)
    return tuple(sorted(forms, key=len, reverse=True))


def _alias_hit(row: TelegramChat, folded_text: str) -> bool:
    """True when the message names this destination.

    Whole-word matching, so a group called "Test" is not found inside
    "testing" - and, more importantly, not inside another group's name.
    """
    for form in alias_forms(row):
        if re.search(rf"(?<![\w]){re.escape(form)}(?![\w])", folded_text):
            return True
    return False


@dataclass(frozen=True, slots=True)
class ChatResolution:
    """The outcome of trying to name one destination.

    Exactly one of ``chat`` and ``candidates`` is meaningful: a single match,
    or several that need a question. Empty both ways means nothing matched.
    """

    chat: TelegramChat | None = None
    candidates: tuple[TelegramChat, ...] = ()
    reason: str = ""

    @property
    def is_resolved(self) -> bool:
        return self.chat is not None

    @property
    def is_ambiguous(self) -> bool:
        return self.chat is None and len(self.candidates) > 1


@dataclass(frozen=True, slots=True)
class RecipientCandidate:
    """One destination a request could mean, and why it is on the list.

    ``source`` is what the preview reads back: a group somebody *named* needs
    no explanation, and one MeoBot inferred from a tag gets "MeoBot hiểu ‘các
    group Content’ là" in front of it. Presenting the two identically is how a
    person ends up confirming a list they never checked.
    """

    chat: TelegramChat
    source: SelectionSource
    matched_phrase: str = ""


@dataclass(frozen=True, slots=True)
class AudienceResolution:
    """Every destination one request resolves to, plus what it could not place.

    Deliberately not a single answer. A multi-group request can succeed for
    three groups, be ambiguous about a fourth and find nothing at all for a
    fifth, and collapsing that into "resolved" or "not resolved" would either
    send to a shorter list than somebody asked for or refuse the whole thing.
    """

    candidates: tuple[RecipientCandidate, ...] = ()
    #: One entry per name that matched several registrations: the phrase, and
    #: the destinations it could mean. Always asked about, never guessed.
    ambiguous: tuple[tuple[str, tuple[TelegramChat, ...]], ...] = ()
    #: Name phrases that matched nothing registered.
    unresolved: tuple[str, ...] = ()
    #: Destinations a "trừ ..." clause removed, kept so the preview can say so.
    excluded: tuple[TelegramChat, ...] = ()
    scope: AudienceScope | None = None
    #: Registered but switched off or not accepting automated delivery.
    paused_count: int = 0
    #: Registered and active, but the bot cannot currently post there.
    unreachable_count: int = 0
    reason: str = ""

    @property
    def is_resolved(self) -> bool:
        return bool(self.candidates)

    @property
    def is_ambiguous(self) -> bool:
        return bool(self.ambiguous)

    @property
    def chats(self) -> tuple[TelegramChat, ...]:
        return tuple(candidate.chat for candidate in self.candidates)


@dataclass(frozen=True, slots=True)
class PrivateResolution:
    """Whether one person can be messaged privately, and where."""

    user: User | None = None
    telegram_chat_id: int | None = None
    available: bool = False
    message: str = ""

    @property
    def is_resolved(self) -> bool:
        return self.available and self.telegram_chat_id is not None


class RecipientResolver:
    """Resolves destinations from stored data only.

    Args:
        session: Read unit of work.
        settings: Supplies the configured owner Telegram id.
    """

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._registry = ChatRegistryService(session)

    # --- Groups -----------------------------------------------------------
    async def resolve_chat(
        self,
        *,
        bot_identity: int,
        text: str = "",
        purpose: ChatPurpose | None = None,
        chat_id: uuid.UUID | None = None,
    ) -> ChatResolution:
        """Find the one destination this request means, or report ambiguity.

        Args:
            text: What the person wrote, used for alias and purpose matching.
            purpose: An explicit purpose, which wins over anything in ``text``.
            chat_id: A destination already chosen in a confirmed flow, which
                wins over everything.
        """
        if chat_id is not None:
            row = await self._registry.by_id(chat_id)
            if row is not None and row.is_active:
                return ChatResolution(chat=row, reason="explicit_selection")
            return ChatResolution(reason="explicit_selection_unavailable")

        active = await self._registry.active(bot_identity=bot_identity)
        if not active:
            return ChatResolution(reason="nothing_registered")

        if purpose is not None:
            matches = [row for row in active if row.purpose is purpose]
            return self._one_or_ask(matches, reason=f"purpose_{purpose.value.lower()}")

        folded = normalize_alias(text)

        # 1. A registered name. Since 0.6.0a2.1 a destination answers to every
        #    name it is actually known by - the stored alias, the display name,
        #    the Telegram title, and each of those with a leading "group" /
        #    "nhóm" / "team" removed - so "Group Test" is reachable as "Test",
        #    "group Test" and "nhóm Test" without three registry entries.
        matched = [row for row in active if _alias_hit(row, folded)]
        if len(matched) == 1:
            return ChatResolution(chat=matched[0], reason="exact_alias")
        if len(matched) > 1:
            # Two registered names both appear - genuinely ambiguous, and the
            # caller asks rather than picking the closest.
            return ChatResolution(candidates=tuple(matched), reason="alias_ambiguous")

        # 2. A named purpose.
        for phrase, candidate_purpose in PURPOSE_PHRASES.items():
            if phrase in folded:
                matches = [row for row in active if row.purpose is candidate_purpose]
                if matches:
                    return self._one_or_ask(
                        matches, reason=f"purpose_phrase_{candidate_purpose.value.lower()}"
                    )

        # 3. A team name.
        team_matches = [
            row
            for row in active
            if row.team and strip_accents(row.team) and strip_accents(row.team) in folded
        ]
        if team_matches:
            return self._one_or_ask(team_matches, reason="team")

        return ChatResolution(reason="no_match")

    # --- Several groups at once (0.6.0a3) ---------------------------------
    async def resolve_audience(
        self,
        *,
        bot_identity: int,
        actor: Actor,
        text: str,
    ) -> AudienceResolution:
        """Every destination one sentence means, from stored data only.

        The order is the guarantee, and it runs from most specific to least:

        1. an explicit exclusion is read first, so "trừ group Test" survives
           whatever selects the rest;
        2. a whole-registry phrase - "tất cả group", "các group đang hoạt
           động", "những group tôi quản lý";
        3. **names** - display name, Telegram title, or an alias somebody
           entered - and if any name matches, only named groups are returned;
        4. **labels** - tags, purpose, team, brand, department - which is what
           turns "các group Content" into a set;
        5. otherwise, nothing, and the phrases that failed come back so the
           refusal can name them.

        A name that matches two registrations is never decided here. It comes
        back in ``ambiguous`` and the caller asks one question.

        The list is scoped to what ``actor`` may actually address, so a Trưởng
        nhóm saying "tất cả group" reaches the groups they manage and cannot
        discover the existence of any other.
        """
        visible = await self._registry.visible_to(actor=actor, bot_identity=bot_identity)
        if not visible:
            return AudienceResolution(reason="nothing_visible")

        usable = [row for row in visible if _is_usable(row)]
        paused = sum(1 for row in visible if not row.is_active or not row.allow_automated_delivery)
        unreachable = sum(
            1
            for row in visible
            if row.is_active and row.allow_automated_delivery and not _can_send(row)
        )

        folded = normalize_alias(text)
        excluded = self._excluded_rows(text, visible)
        excluded_ids = {row.id for row in excluded}
        pool = [row for row in usable if row.id not in excluded_ids]

        def finish(
            candidates: list[RecipientCandidate],
            *,
            reason: str,
            scope: AudienceScope | None = None,
            ambiguous: tuple[tuple[str, tuple[TelegramChat, ...]], ...] = (),
            unresolved: tuple[str, ...] = (),
        ) -> AudienceResolution:
            return AudienceResolution(
                candidates=tuple(candidates),
                ambiguous=ambiguous,
                unresolved=unresolved,
                excluded=tuple(excluded),
                scope=scope,
                paused_count=paused,
                unreachable_count=unreachable,
                reason=reason,
            )

        # --- 2. The whole registry, described rather than named ------------
        scope = read_audience_scope(text)
        if scope is not None:
            rows = pool
            if scope is AudienceScope.MANAGED_BY_ME:
                rows = await self._managed_subset(pool, actor=actor)
            elif scope is AudienceScope.MOST_RECENT:
                # ``active`` orders newest first, so "group vừa đăng ký" is the
                # head of the list - and it is still shown by name before
                # anything is sent, because "the one I just added" is a memory.
                rows = pool[:1]
            source = (
                SelectionSource.ALL_REGISTERED
                if scope is AudienceScope.ALL_REGISTERED
                else SelectionSource.INFERRED
            )
            return finish(
                [RecipientCandidate(chat=row, source=source) for row in rows],
                reason=f"scope_{scope.value.lower()}",
                scope=scope,
            )

        # --- 3. Names ------------------------------------------------------
        by_form: dict[str, list[TelegramChat]] = {}
        for row in pool:
            for form in _matched_forms(row, folded):
                by_form.setdefault(form, []).append(row)

        conflicting = tuple((form, tuple(rows)) for form, rows in by_form.items() if len(rows) > 1)
        if conflicting:
            # Two registrations answer to one name. Picking the more recent one
            # would be right about half the time, and wrong in a way that posts
            # an internal announcement into a group nobody meant.
            return finish([], reason="alias_ambiguous", ambiguous=conflicting)

        named = _unique([rows[0] for rows in by_form.values()])
        if named:
            return finish(
                [
                    RecipientCandidate(
                        chat=row,
                        source=SelectionSource.NAMED,
                        matched_phrase=_matched_forms(row, folded)[0],
                    )
                    for row in named
                ],
                reason="named",
            )

        # --- 4. Labels: tags, purpose, team, brand, department -------------
        labelled = [
            RecipientCandidate(chat=row, source=SelectionSource.INFERRED, matched_phrase=form)
            for row in pool
            for form in _label_forms(row)[:1]
            if any(_whole_word(candidate, folded) for candidate in _label_forms(row))
        ]
        if labelled:
            return finish(labelled, reason="labelled")

        for phrase, candidate_purpose in PURPOSE_PHRASES.items():
            if phrase not in folded:
                continue
            matches = [row for row in pool if row.purpose is candidate_purpose]
            if matches:
                return finish(
                    [
                        RecipientCandidate(
                            chat=row, source=SelectionSource.INFERRED, matched_phrase=phrase
                        )
                        for row in matches
                    ],
                    reason=f"purpose_{candidate_purpose.value.lower()}",
                )

        # --- 5. Nothing placed --------------------------------------------
        from meobot.domain.dispatch.phrases import read_names

        return finish([], reason="no_match", unresolved=read_names(text))

    def _excluded_rows(self, text: str, visible: Sequence[TelegramChat]) -> list[TelegramChat]:
        """Destinations a "trừ ... " / "bỏ ..." clause names."""
        phrases = read_exclusions(text)
        if not phrases:
            return []
        removed: list[TelegramChat] = []
        for phrase in phrases:
            folded = normalize_alias(phrase)
            for row in visible:
                if row not in removed and any(
                    form == folded or _whole_word(form, folded) for form in alias_forms(row)
                ):
                    removed.append(row)
        return removed

    async def _managed_subset(
        self, pool: Sequence[TelegramChat], *, actor: Actor
    ) -> list[TelegramChat]:
        """The groups this person is explicitly assigned to manage."""
        if actor.user_id is None:
            return list(pool)
        from meobot.application.chat_assignment_service import ChatAssignmentService

        managed = await ChatAssignmentService(self._session).managed_chats(user_id=actor.user_id)
        allowed = {row.id for row in managed}
        return [row for row in pool if row.id in allowed]

    @staticmethod
    def match_names(
        names: Sequence[str], pool: Sequence[TelegramChat]
    ) -> tuple[list[TelegramChat], list[str]]:
        """Match folded name phrases against a list of destinations.

        Used for a reply to an open draft - "Saykeng với Test", "bỏ Test" - so
        the words somebody typed are checked against destinations they were
        actually shown rather than against the whole registry.

        Returns:
            ``(matched, unmatched)``. An unmatched phrase is reported, never
            approximated.
        """
        matched: list[TelegramChat] = []
        unmatched: list[str] = []
        for name in names:
            folded = normalize_alias(name)

            def names_it(row: TelegramChat, folded: str = folded) -> bool:
                return any(form == folded or _whole_word(form, folded) for form in alias_forms(row))

            def describes_it(row: TelegramChat, folded: str = folded) -> bool:
                return any(_whole_word(form, folded) for form in _label_forms(row))

            free = [row for row in pool if row not in matched]
            hit = next((row for row in free if names_it(row)), None)
            if hit is None:
                hit = next((row for row in free if describes_it(row)), None)
            if hit is None:
                unmatched.append(name)
            else:
                matched.append(hit)
        return matched, unmatched

    @staticmethod
    def _one_or_ask(matches: Sequence[TelegramChat], *, reason: str) -> ChatResolution:
        """One match resolves; several ask; none reports nothing."""
        if len(matches) == 1:
            return ChatResolution(chat=matches[0], reason=reason)
        if len(matches) > 1:
            return ChatResolution(candidates=tuple(matches), reason=f"{reason}_ambiguous")
        return ChatResolution(reason=f"{reason}_missing")

    # --- People -----------------------------------------------------------
    async def private_destination(self, *, user_id: uuid.UUID | None) -> PrivateResolution:
        """Where to message one person privately, if we can.

        A username is never used: it is not a chat id, it can change, and
        Telegram will not accept it as a destination for a bot that the person
        has not started.
        """
        if user_id is None:
            return PrivateResolution(message=NO_DESTINATION)
        user = await self._session.get(User, user_id)
        if user is None:
            return PrivateResolution(message=NO_DESTINATION)

        name = self._short_name(user.full_name)
        if user.status is not UserStatus.ACTIVE:
            return PrivateResolution(
                user=user,
                message=f"Tài khoản của {name} hiện không sử dụng được TasksBot.",
            )

        chat_id = user.telegram_private_chat_id or user.telegram_user_id
        if chat_id is None or not user.private_chat_available:
            return PrivateResolution(user=user, message=PRIVATE_UNAVAILABLE.format(name=name))
        return PrivateResolution(user=user, telegram_chat_id=chat_id, available=True)

    async def owner(self) -> PrivateResolution:
        """The configured Trưởng phòng, as a private destination.

        Falls back to the configured Telegram id when no ``users`` row exists
        yet: the bootstrap owner is configuration, and must be reachable before
        anybody has registered them.
        """
        owner_telegram_id = self._settings.meobot_owner_telegram_id
        if owner_telegram_id is None:
            return PrivateResolution(message=NO_DESTINATION)

        result = await self._session.execute(
            select(User).where(User.telegram_user_id == owner_telegram_id)
        )
        user = result.scalar_one_or_none()
        if user is None:
            # Configured but not registered. The id came from configuration,
            # not from a message, so it is authoritative.
            return PrivateResolution(telegram_chat_id=owner_telegram_id, available=True)
        return await self.private_destination(user_id=user.id)

    @staticmethod
    def _short_name(full_name: str) -> str:
        parts = (full_name or "").split()
        return parts[-1] if parts else "người này"
