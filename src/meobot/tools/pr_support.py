"""What every PR tool needs before it can do anything.

Three things, and each is here rather than in the tools so that eleven handlers
cannot drift into eleven slightly different versions of it:

* **wiring** - :func:`pr_services` builds the whole service bundle on the
  session the tool was handed. Every service shares that one session, so a tool
  that resolves a code, allocates a task number and writes a row does all three
  inside the single transaction
  :meth:`~meobot.application.conversation_service.ConversationService._run_tool`
  opened. No tool ever calls ``commit``;
* **resolution** - turning "CNT-2026-000123" or "bài nâng mũi" into one entity,
  or into a question. Never into a guess;
* **vocabulary** - mapping "duyệt trưởng nhóm" onto ``PR_TEAM_LEAD_REVIEW`` so
  a person never has to type an enum constant, while the enum stays the only
  thing that crosses into a service.

What is deliberately *not* here: any decision about whether an action is
allowed. Capability checks live in
:class:`~meobot.application.pr_capability_service.PrCapabilityService` and run
inside the services, where a future web client gets them too. A tool that
pre-checked would be a second authority, and the two would eventually disagree.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from meobot.application.pr_people_resolver import PersonResolution
from meobot.application.pr_services import PrServices, build_pr_services
from meobot.application.work_schedule_service import WorkScheduleService
from meobot.core.errors import ToolExecutionError
from meobot.db.models.pr import PrChannel, PrContentItem
from meobot.domain.hr.schedule import resolve_date
from meobot.domain.identity.models import Actor
from meobot.domain.member.normalization import strip_accents
from meobot.domain.pr.policy import PrCapability
from meobot.tools.base import ToolContext
from meobot.tools.pr_presenters import format_content_choices, format_person_choices

#: A content code as the team writes it. Used only to decide whether a phrase
#: is an identifier or a description - never to parse meaning out of one.
CONTENT_CODE_PREFIXES = ("CNT-", "TSK-", "CH-", "PUB-", "ISS-")


def pr_services(context: ToolContext) -> PrServices:
    """Build the PR service bundle on the tool's session.

    Delegates to :func:`~meobot.application.pr_services.build_pr_services`.
    Since Step 1E the web API needs the same bundle, so the wiring lives in the
    application layer and this function is only the Telegram adapter for it -
    unwrapping ``ToolContext`` into the session and settings the builder wants.
    Two copies of that dependency graph would eventually differ by one edge.
    """
    return build_pr_services(context.require_session(), context.settings)


def timezone_of(context: ToolContext) -> ZoneInfo:
    """The display timezone, from the one place it is configured."""
    return ZoneInfo(context.settings.app_timezone)


# --- Resolving what somebody typed -----------------------------------------


def looks_like_code(reference: str) -> bool:
    """True when a phrase is an identifier rather than a description."""
    return reference.strip().upper().startswith(CONTENT_CODE_PREFIXES)


async def resolve_content(services: PrServices, *, actor: Actor, reference: str) -> PrContentItem:
    """One content item from a code or a description.

    A code resolves exactly. Anything else searches, and **one hit resolves
    while several ask** - the caller gets a
    :class:`~meobot.core.errors.ToolExecutionError` carrying the choices, which
    the conversation surface shows as a question. Picking the best match would
    be right most of the time, and the times it was wrong somebody would have
    approved the wrong piece of work.
    """
    phrase = reference.strip()
    if looks_like_code(phrase):
        return await services.queries.get_content_by_code(actor=actor, code=phrase)

    candidates = list(await services.queries.search_contents(actor=actor, text=phrase))
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        raise ToolExecutionError(
            format_content_choices(candidates, phrase=phrase),
            details={
                "reason": "ambiguous_content",
                "candidates": [content.code for content in candidates],
            },
        )
    raise ToolExecutionError(
        f'Mình không tìm thấy nội dung nào khớp với "{phrase}".',
        details={"reason": "content_not_found", "query": phrase},
    )


async def resolve_person(services: PrServices, *, name: str) -> uuid.UUID:
    """One ``users.id`` from a name, or a question.

    Never creates anybody. "No such person" is an answer; silently registering
    a new user because somebody misspelled a name is how a directory fills up
    with ghosts.
    """
    resolution: PersonResolution = await services.people.resolve(name)
    if resolution.match is not None:
        return resolution.match.user_id
    if resolution.ambiguous:
        raise ToolExecutionError(
            format_person_choices(
                [candidate.full_name for candidate in resolution.candidates], phrase=name
            ),
            details={
                "reason": "ambiguous_person",
                "candidates": [str(c.user_id) for c in resolution.candidates],
            },
        )
    raise ToolExecutionError(
        f'Mình không tìm thấy người nào tên "{name}" trong MeoBot.',
        details={"reason": "person_not_found", "query": name},
    )


async def resolve_channel(services: PrServices, *, actor: Actor, reference: str) -> PrChannel:
    """One channel from a code or a name, or a question."""
    phrase = reference.strip()
    candidates = list(await services.queries.search_channels(actor=actor, text=phrase))
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        listed = "\n".join(
            f"{index}. {channel.code} — {channel.name}"
            for index, channel in enumerate(candidates, start=1)
        )
        raise ToolExecutionError(
            f'Mình tìm thấy {len(candidates)} kênh khớp với "{phrase}":\n{listed}\n'
            "Bạn cho mình mã kênh cụ thể nhé.",
            details={
                "reason": "ambiguous_channel",
                "candidates": [channel.code for channel in candidates],
            },
        )
    raise ToolExecutionError(
        f'Mình không tìm thấy kênh nào khớp với "{phrase}".',
        details={"reason": "channel_not_found", "query": phrase},
    )


async def person_names(services: PrServices, user_ids: Sequence[uuid.UUID]) -> dict[str, str]:
    """``{user_id: full name}``, for rendering ids back to people."""
    names: dict[str, str] = {}
    for user_id in user_ids:
        match = await services.people.by_id(user_id)
        if match is not None:
            names[str(user_id)] = match.full_name
    return names


# --- Dates people actually type ---------------------------------------------


async def resolve_deadline(
    services: PrServices, *, text: str | None, context: ToolContext
) -> datetime | None:
    """ "thứ Sáu" into an instant, using the one parser MeoBot already has.

    Delegates to :func:`meobot.domain.hr.schedule.resolve_date`, which is where
    "hôm nay", "ngày mai", weekday names and ``31/8`` are already understood.
    Writing a second parser would mean a phrase could mean one day to a leave
    request and another to a PR deadline.

    The time of day is the **end of the working day** in the configured
    timezone: a deadline of "thứ Sáu" means end of Friday, not midnight at the
    start of it, and treating it otherwise would make a task overdue a day
    early.

    Returns ``None`` when there is nothing to parse. An *unparseable* phrase
    raises instead, so a caller never silently loses the date somebody typed.
    """
    if not text or not text.strip():
        return None

    schedule = await WorkScheduleService(services.session).for_display()
    zone = timezone_of(context)
    now = datetime.now(tz=UTC)

    parsed: date | None = _iso_date(text) or resolve_date(text, now=now, schedule=schedule)
    if parsed is None:
        raise ToolExecutionError(
            f'Mình chưa hiểu mốc thời gian "{text}". '
            "Bạn nói rõ hơn giúp mình nhé (ví dụ: hôm nay, thứ Sáu, 31/8).",
            details={"reason": "unparsed_date", "query": text},
        )
    return datetime.combine(parsed, schedule.afternoon_end, tzinfo=zone).astimezone(UTC)


async def resolve_day(
    services: PrServices, *, text: str | None, context: ToolContext
) -> date | None:
    """The same parsing, for the date-only fields channel assignments use."""
    if not text or not text.strip():
        return None
    schedule = await WorkScheduleService(services.session).for_display()
    parsed = _iso_date(text) or resolve_date(text, now=datetime.now(tz=UTC), schedule=schedule)
    if parsed is None:
        raise ToolExecutionError(
            f'Mình chưa hiểu ngày "{text}". Bạn nói rõ hơn giúp mình nhé.',
            details={"reason": "unparsed_date", "query": text},
        )
    return parsed


def _iso_date(text: str) -> date | None:
    """``2026-08-31`` from a caller that already had a real date.

    Checked before the Vietnamese parser because an LLM filling a tool argument
    will often normalise to ISO, and round-tripping that through a phrase
    matcher would be a needless chance to get it wrong.
    """
    try:
        return date.fromisoformat(text.strip())
    except ValueError:
        return None


def at_end_of_day(day: date, *, tz: ZoneInfo) -> datetime:
    """Local end-of-day as UTC, for callers that already have a date."""
    return datetime.combine(day, time(23, 59), tzinfo=tz).astimezone(UTC)


# --- Capability names people say --------------------------------------------

#: Phrases a person might use for each review capability, accent-folded. Only
#: the three grant-backed ones: the other seven are decided by role and there
#: is nothing to grant, so offering to "cấp quyền tạo nội dung" would be a
#: sentence the system cannot honour.
CAPABILITY_PHRASES: Mapping[str, PrCapability] = {
    "team lead review": PrCapability.PR_TEAM_LEAD_REVIEW,
    "team lead": PrCapability.PR_TEAM_LEAD_REVIEW,
    "duyet team lead": PrCapability.PR_TEAM_LEAD_REVIEW,
    "duyet truong nhom": PrCapability.PR_TEAM_LEAD_REVIEW,
    "truong nhom duyet": PrCapability.PR_TEAM_LEAD_REVIEW,
    "truong nhom": PrCapability.PR_TEAM_LEAD_REVIEW,
    "pr_team_lead_review": PrCapability.PR_TEAM_LEAD_REVIEW,
    "head review": PrCapability.PR_HEAD_REVIEW,
    "head": PrCapability.PR_HEAD_REVIEW,
    "duyet head": PrCapability.PR_HEAD_REVIEW,
    "duyet truong phong": PrCapability.PR_HEAD_REVIEW,
    "truong phong duyet": PrCapability.PR_HEAD_REVIEW,
    "truong phong": PrCapability.PR_HEAD_REVIEW,
    "pr_head_review": PrCapability.PR_HEAD_REVIEW,
    "internal review": PrCapability.PR_INTERNAL_REVIEW,
    "duyet noi bo": PrCapability.PR_INTERNAL_REVIEW,
    "noi bo": PrCapability.PR_INTERNAL_REVIEW,
    "pr_internal_review": PrCapability.PR_INTERNAL_REVIEW,
}


def resolve_capability(phrase: str) -> PrCapability:
    """ "duyệt trưởng nhóm" into ``PR_TEAM_LEAD_REVIEW``.

    A fixed table, not a fuzzy matcher. The bot already has semantic tool
    selection; what this needs to do is map a small closed vocabulary onto
    three enum members without ever landing on the wrong gate, and a lookup
    that fails loudly does that better than a scorer that always answers.
    """
    folded = strip_accents(phrase or "").strip()
    capability = CAPABILITY_PHRASES.get(folded)
    if capability is not None:
        return capability
    for spelling, candidate in CAPABILITY_PHRASES.items():
        if spelling in folded:
            return candidate
    raise ToolExecutionError(
        f'Mình chưa hiểu quyền "{phrase}". '
        "Hiện có: Duyệt Trưởng nhóm, Duyệt Trưởng phòng, Duyệt nội bộ.",
        details={"reason": "unknown_capability", "query": phrase},
    )


__all__: list[str] = [
    "CAPABILITY_PHRASES",
    "PrServices",
    "at_end_of_day",
    "looks_like_code",
    "person_names",
    "pr_services",
    "resolve_capability",
    "resolve_channel",
    "resolve_content",
    "resolve_day",
    "resolve_deadline",
    "resolve_person",
    "timezone_of",
]
