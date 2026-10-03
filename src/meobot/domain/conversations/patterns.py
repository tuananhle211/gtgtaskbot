"""Deterministic routing and deterministic replies.

Two jobs, both of which exist so that conversation does not depend on a model
being reachable.

**Routing without a model.** Some messages do not need one. "Hello" is a
greeting; "đồng bộ các Sheet" is an operation; "duyệt hết đi" is ambiguous. A
keyword match settles those faster, cheaper and more reliably than a round-trip,
and - the point - it still settles them when the provider is down. The router is
only asked about the messages in between.

**Replies without a model.** When chat generation fails twice, the user still
gets something a colleague would say. What they must never get is the sentence
this module was written to delete: *"Bạn thử nhắn lại ngắn gọn hơn"* in response
to "Hello". There is no shorter way to write "Hello", so that reply told the
user their own message was the problem when the provider was.

Safety rule for the deterministic router: it may route to ``tool`` **only** on
an unambiguous operational verb, and the resulting plan still goes through the
policy engine, the permission check and the confirmation gate. When it is
unsure it routes to ``chat``, because a wrong ``chat`` costs one wasted answer
and a wrong ``tool`` touches real data.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from meobot.integrations.llm.base import MessageRoute

#: ``đ`` is a distinct Vietnamese letter, not ``d`` with a diacritic, so Unicode
#: normalisation leaves it alone. Folding it by hand is what makes "đồng bộ"
#: match "dong bo" - without this line every trigger phrase containing ``đ``
#: ("đồng bộ", "làm được gì", "đã tạo", "được sử dụng") silently never matched.
_DSTROKE = str.maketrans({"đ": "d", "Đ": "d"})


def fold(text: str) -> str:
    """Lower-case and strip Vietnamese diacritics for matching."""
    decomposed = unicodedata.normalize("NFD", text.lower().translate(_DSTROKE))
    return "".join(char for char in decomposed if unicodedata.category(char) != "Mn")


#: Phrases whose target is genuinely ambiguous. Never resolved by guessing -
#: "duyệt hết đi" must not approve anything.
AMBIGUOUS_PATTERNS: tuple[tuple[str, str], ...] = (
    ("duyet het", "những kịch bản nào cần duyệt"),
    ("duyet tat ca", "những kịch bản nào cần duyệt"),
    ("duyet luon", "kịch bản nào cần duyệt"),
    ("duyet di", "kịch bản nào cần duyệt"),
    ("xoa cai do", "bạn muốn dừng đồng bộ Sheet nào"),
    ("xoa het", "bạn muốn dừng đồng bộ Sheet nào"),
    ("sua no", "bạn muốn sửa kịch bản nào"),
    ("sua cai do", "bạn muốn sửa kịch bản nào"),
    ("gui cho ho", "bạn muốn gửi cho ai"),
    ("lam giup cai do", "bạn muốn làm gì, với đối tượng nào"),
)

#: Verbs that mean "do something to real data". A message containing one of
#: these is operational even when the router is unavailable.
OPERATIONAL_VERBS: tuple[str, ...] = (
    "dong bo",
    "sync",
    "tao sheet",
    "tao file",
    "tao bang",
    "tao ma moi",
    "tao invite",
    "duyet",
    "approve",
    "huy",
    "review kich ban",
    "cham diem kich ban",
    "dang ky thu muc",
    "them sheet",
)

#: Nouns that make a verb operational rather than conversational. "tạo một ý
#: tưởng" is chat; "tạo Sheet" is not.
OPERATIONAL_OBJECTS: tuple[str, ...] = (
    "sheet",
    "spreadsheet",
    "bang tinh",
    "drive",
    "thu muc",
    "kich ban",
    "script",
    "ma moi",
    "invite",
    "he thong",
)

#: Single words that open a conversation. Checked per token, never as a
#: substring, so "chào bạn, đồng bộ Sheet giúp mình" is not caught by "chao".
GREETING_PATTERNS: tuple[str, ...] = (
    "hello",
    "hi",
    "hey",
    "chao",
    "alo",
    "yo",
)

#: Whole messages that are greetings even though their words are not.
GREETING_PHRASES: tuple[str, ...] = (
    "co do khong",
    "con thuc khong",
    "co day khong",
)

#: "Bạn làm được gì?" and its variants. Answered from the live registry.
CAPABILITY_PATTERNS: tuple[str, ...] = (
    "lam duoc gi",
    "lam duoc nhung gi",
    "giup duoc gi",
    "co the lam gi",
    "chuc nang gi",
    "kha nang cua ban",
    "what can you do",
    "ban giup toi duoc gi",
)

#: "Bạn là ai?"
IDENTITY_PATTERNS: tuple[str, ...] = (
    "ban la ai",
    "ban ten gi",
    "meobot la gi",
    "meobot la ai",
    "gioi thieu ve ban",
    "gioi thieu ban than",
    "who are you",
)

#: "Bạn đang nói chuyện với ai?"
ACTOR_PATTERNS: tuple[str, ...] = (
    "dang noi chuyen voi ai",
    "toi la ai",
    "biet toi la ai",
    "ho so cua toi",
    "biet gi ve toi",
)

_WORD_BOUNDARY = re.compile(r"[^a-z0-9]+")


def _tokens(folded: str) -> list[str]:
    return [token for token in _WORD_BOUNDARY.split(folded) if token]


#: Words that can sit next to a greeting without turning it into a request:
#: "chào bạn nhé", "alo MeoBot ơi". Anything outside this set is content.
_GREETING_FILLER: frozenset[str] = frozenset(
    {
        "xin",
        "ban",
        "meobot",
        "oi",
        "nhe",
        "nha",
        "a",
        "e",
        "em",
        "anh",
        "chi",
        "moi",
        "buoi",
        "sang",
        "trua",
        "chieu",
        "toi",
        "there",
        "bot",
    }
)


def is_greeting(message: str) -> bool:
    """True for a bare greeting - a message that is *only* an opener.

    A length cap is not enough: "chào bạn, đồng bộ các Sheet" is short *and*
    starts with a greeting, and short-circuiting it to a canned hello would
    silently drop a real request. So every token has to be either a greeting
    word or a harmless filler; one content word and this is not a greeting.
    """
    folded = fold(message).strip(" .!?,")
    if folded in GREETING_PHRASES:
        return True
    tokens = _tokens(folded)
    if not tokens or len(tokens) > 6:
        return False
    if not any(token in GREETING_PATTERNS for token in tokens):
        return False
    return all(token in GREETING_PATTERNS or token in _GREETING_FILLER for token in tokens)


def _matches_any(message: str, patterns: tuple[str, ...]) -> bool:
    folded = fold(message)
    return any(pattern in folded for pattern in patterns)


def asks_about_capabilities(message: str) -> bool:
    """True for "Bạn làm được gì?" and friends."""
    return _matches_any(message, CAPABILITY_PATTERNS)


def asks_about_identity(message: str) -> bool:
    """True for "Bạn là ai?"."""
    return _matches_any(message, IDENTITY_PATTERNS)


def asks_about_actor(message: str) -> bool:
    """True for "Bạn đang nói chuyện với ai?"."""
    return _matches_any(message, ACTOR_PATTERNS)


def ambiguous_target(message: str) -> str | None:
    """The missing fact when the message names an action but not its target."""
    folded = fold(message)
    for pattern, missing in AMBIGUOUS_PATTERNS:
        if pattern in folded:
            return missing
    return None


def looks_operational(message: str) -> bool:
    """True when the message asks for something to be *done* to real data.

    Requires a verb **and** an object, so "cho mình vài ý tưởng để duyệt lại
    cách làm nội dung" is not mistaken for an approval request.
    """
    folded = fold(message)
    has_verb = any(verb in folded for verb in OPERATIONAL_VERBS)
    has_object = any(noun in folded for noun in OPERATIONAL_OBJECTS)
    return has_verb and has_object


@dataclass(frozen=True, slots=True)
class DeterministicRoute:
    """A route settled without asking a model, plus why."""

    route: MessageRoute
    #: Set when the reply itself is deterministic too (greeting, capabilities).
    canned_reply_kind: str | None = None


def deterministic_route(message: str) -> DeterministicRoute | None:
    """Route a message without a model, or return ``None`` to ask one.

    Order is chosen for safety: ambiguity beats operation, and operation beats
    conversation, so no phrasing can promote itself past a clarification.
    """
    missing = ambiguous_target(message)
    if missing is not None:
        return DeterministicRoute(
            MessageRoute.clarify(missing=missing, confidence=0.95, label="pattern:ambiguous")
        )

    if looks_operational(message):
        return DeterministicRoute(MessageRoute.tool(confidence=0.8, label="pattern:operational"))

    if asks_about_capabilities(message):
        return DeterministicRoute(
            MessageRoute.chat(confidence=0.95, label="pattern:capabilities"),
            canned_reply_kind="capabilities",
        )
    if asks_about_identity(message):
        return DeterministicRoute(
            MessageRoute.chat(confidence=0.95, label="pattern:identity"),
            canned_reply_kind="identity",
        )
    if asks_about_actor(message):
        return DeterministicRoute(
            MessageRoute.chat(confidence=0.95, label="pattern:actor"),
            canned_reply_kind="actor",
        )
    if is_greeting(message):
        return DeterministicRoute(
            MessageRoute.chat(confidence=0.95, label="pattern:greeting"),
            canned_reply_kind="greeting",
        )
    return None


def fallback_route(message: str) -> MessageRoute:
    """Where a message goes when the router itself failed.

    Chat-first: an ordinary message becomes conversation, and only something
    that *looks* operational becomes a clarification. Never a tool - a guessed
    action is the one outcome a provider failure must not be able to produce.
    """
    missing = ambiguous_target(message)
    if missing is not None:
        return MessageRoute.clarify(missing=missing, confidence=0.4, label="fallback:ambiguous")
    if looks_operational(message):
        return MessageRoute.clarify(
            missing="thao tác và đối tượng cụ thể",
            confidence=0.3,
            label="fallback:operational",
        )
    return MessageRoute.chat(confidence=0.3, label="fallback:chat")
