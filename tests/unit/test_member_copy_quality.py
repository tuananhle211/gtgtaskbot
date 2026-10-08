"""Scanning everything a Member reads for words they should never see.

The rule "no enum, no English status, no identifier reaches a Member" cannot be
enforced by review - somebody will add a string next month. It can be enforced
by a test, but only because the copy lives in one registry
(:mod:`meobot.domain.member.copy`) instead of being scattered through handlers.
That is the argument for the registry, and this file is what collects on it.

Platform names stay: Facebook, TikTok, Telegram, Google Drive and "MeoBot" are
what people call these things out loud. "Member" and "Guest" stay too - they are
the display labels this deployment chose, in the role table.
"""

from __future__ import annotations

import re

import pytest

from meobot.application.access_gate import BLOCKED_REVOKED, BLOCKED_SUSPENDED
from meobot.application.hr_request_service import (
    ALREADY_DECIDED,
    CANNOT_APPROVE_OWN,
    DUPLICATE_LATE,
    INVALID_RANGE,
    NOT_YOUR_REQUEST,
    ONLY_OWNER_APPROVES,
    OVERLAPPING,
    PAST_DATE,
)
from meobot.application.work_schedule_service import NOT_CONFIGURED
from meobot.domain.hr.models import HrRequestStatus, HrRequestType, status_label, type_label
from meobot.domain.identity.labels import ROLE_LABELS
from meobot.domain.member import copy

#: Interface English a Member must never be shown. Each of these has a
#: Vietnamese label in the registry; seeing the English one means somebody wrote
#: a string by hand instead of using it.
FORBIDDEN_WORDS: frozenset[str] = frozenset(
    {
        "submit",
        "confirm",
        "cancel",
        "task",
        "dashboard",
        "progress",
        "proof",
        "pending",
        "completed",
        "failed",
        "unauthorized",
        "quota",
        "permission",
        "denied",
        "error",
        "invalid",
        "success",
        "status",
        "request",
        "approve",
        "reject",
    }
)

#: Words that are allowed to appear despite looking like interface English.
ALLOWED = {word.lower() for word in copy.ALLOWED_PROPER_NOUNS} | {
    "ai",  # the abbreviation people actually say in Vietnamese offices
    "page",  # borrowed into Vietnamese for a Facebook page
    "group",  # likewise
    "link",  # likewise
    "admin",  # "admin group" is what a Vietnamese team calls the moderator
}

_WORD = re.compile(r"[A-Za-z]+")


def offending_words(text: str) -> set[str]:
    """Latin-script words in ``text`` that a Member must not be shown."""
    found = {match.group(0).lower() for match in _WORD.finditer(text)}
    return {word for word in found & FORBIDDEN_WORDS if word not in ALLOWED}


def member_facing_strings() -> dict[str, str]:
    """Every registered string a Member can end up reading."""
    strings: dict[str, str] = {}

    for button in copy.Button:
        strings[f"Button.{button.name}"] = button.value
    for problem in copy.Problem:
        strings[f"Problem.{problem.name}"] = problem.value
    for label in copy.WorkStatusLabel:
        strings[f"WorkStatusLabel.{label.name}"] = label.value
    for key, value in copy.TERMS.items():
        strings[f"TERMS[{key}]"] = value
    for key, value in copy.NOT_BUILT_YET.items():
        strings[f"NOT_BUILT_YET[{key}]"] = value

    for name in (
        "HOME_GREETING",
        "HOME_QUESTION",
        "HOME_NOTHING_TODAY",
        "HELP_MEMBER",
        "AI_ALLOWANCE",
        "AI_ALLOWANCE_UNLIMITED",
        "AI_ALLOWANCE_LOW",
        "ASK_WHICH_WORK",
        "ASK_HOW_MUCH_DONE",
        "ASK_FOR_PROOF",
        "ASK_WHICH_TASK_FOR_PHOTO",
        "FLOW_EXPIRED",
        "NOTHING_TO_CONTINUE",
        "CANCELLED",
        "NOT_UNDERSTOOD",
    ):
        strings[f"copy.{name}"] = getattr(copy, name)

    for status in HrRequestStatus:
        strings[f"status_label({status.name})"] = status_label(status)
    for kind in HrRequestType:
        strings[f"type_label({kind.name})"] = type_label(kind)
    for role, label in ROLE_LABELS.items():
        strings[f"role_label({role.name})"] = label

    for name, value in (
        ("BLOCKED_SUSPENDED", BLOCKED_SUSPENDED),
        ("BLOCKED_REVOKED", BLOCKED_REVOKED),
        ("CANNOT_APPROVE_OWN", CANNOT_APPROVE_OWN),
        ("NOT_YOUR_REQUEST", NOT_YOUR_REQUEST),
        ("ONLY_OWNER_APPROVES", ONLY_OWNER_APPROVES),
        ("ALREADY_DECIDED", ALREADY_DECIDED),
        ("PAST_DATE", PAST_DATE),
        ("OVERLAPPING", OVERLAPPING),
        ("DUPLICATE_LATE", DUPLICATE_LATE),
        ("INVALID_RANGE", INVALID_RANGE),
        ("NOT_CONFIGURED", NOT_CONFIGURED),
    ):
        strings[f"hr.{name}"] = value

    return strings


@pytest.mark.parametrize(("name", "text"), sorted(member_facing_strings().items()))
def test_no_member_facing_string_contains_interface_english(name: str, text: str) -> None:
    offenders = offending_words(text)
    assert not offenders, f"{name} shows English interface words: {sorted(offenders)}"


@pytest.mark.parametrize(("name", "text"), sorted(member_facing_strings().items()))
def test_no_member_facing_string_contains_an_enum_value(name: str, text: str) -> None:
    """The exact strings the brief forbids, checked one by one."""
    for enum_value in (
        "ASSIGNED",
        "IN_PROGRESS",
        "PENDING_GROUP_APPROVAL",
        "SUBMIT_PROOF",
        "TOOL_NOT_ALLOWED",
        "quota_exhausted",
        "permission_denied",
        "FULL_DAY_LEAVE",
        "LATE_ARRIVAL",
        "CHANGE_REQUESTED",
        "EMPLOYEE",
        "TEAM_LEAD",
    ):
        assert enum_value not in text, f"{name} exposes {enum_value}"


@pytest.mark.parametrize(("name", "text"), sorted(member_facing_strings().items()))
def test_no_member_facing_string_contains_an_identifier(name: str, text: str) -> None:
    """No UUIDs, no ``task_id=``, no ``script_id=``."""
    assert not re.search(r"\b[0-9a-f]{8}-[0-9a-f]{4}-", text), f"{name} contains a UUID"
    assert not re.search(r"\b\w+_id\s*=", text), f"{name} contains an identifier"


def test_every_button_label_is_vietnamese_or_a_platform_name() -> None:
    for button in copy.Button:
        offenders = offending_words(button.value)
        assert not offenders, f"{button.name} is English: {sorted(offenders)}"


def test_the_forbidden_words_would_actually_be_caught() -> None:
    """A guard for the guard: an English button must fail this scanner."""
    assert offending_words("Submit") == {"submit"}
    assert offending_words("Confirm this task") == {"confirm", "task"}
    assert offending_words("Permission denied") == {"permission", "denied"}


def test_platform_names_are_not_flagged() -> None:
    assert offending_words("Đăng lên Facebook và TikTok qua Google Drive") == set()
    assert offending_words("TasksBot gửi qua Telegram") == set()


def test_the_help_card_teaches_natural_language_first() -> None:
    """A Member should be told to talk, not handed a command list."""
    assert "Bạn không cần nhớ câu lệnh" in copy.HELP_MEMBER
    assert "/" not in copy.HELP_MEMBER, "the Member help must not list slash commands"


def test_the_help_card_hides_management_capabilities() -> None:
    """Nothing a Member would only be refused appears in their help."""
    for forbidden in ("OAuth", "token", "vai trò", "duyệt đơn", "toàn phòng", "API"):
        assert forbidden not in copy.HELP_MEMBER


def test_the_allowance_card_avoids_the_english_word() -> None:
    rendered = copy.AI_ALLOWANCE.format(remaining=12, limit=20)
    assert "quota" not in rendered.lower()
    assert "lượt trò chuyện AI" in rendered


def test_unavailable_features_say_so_rather_than_pretending() -> None:
    for area, text in copy.NOT_BUILT_YET.items():
        assert "đang được hoàn thiện" in text, f"{area} does not admit it is unfinished"


def test_the_home_card_reads_like_a_person_wrote_it() -> None:
    rendered = copy.home_card(name="Linh", lines=["3 việc chưa hoàn thành"])
    assert rendered.startswith("Chào Linh")
    assert "Bạn muốn làm gì?" in rendered
    assert offending_words(rendered) == set()
