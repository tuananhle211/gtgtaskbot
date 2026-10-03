"""Signed callback data and invite-code rules.

Both are places where client-supplied input decides what happens next, so both
are tested for the refusal path first.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from meobot.core.time import utcnow
from meobot.domain.conversations.callbacks import (
    MAX_CALLBACK_BYTES,
    CallbackAction,
    build_callback,
    parse_callback,
)
from meobot.domain.identity.invites import (
    CODE_LENGTH,
    InviteFacts,
    InviteRejection,
    can_invite_role,
    check_invite,
    codes_match,
    generate_code,
    hash_code,
    normalize_code,
)
from meobot.domain.identity.models import Role

SECRET = "test-callback-secret"
OTHER_SECRET = "a-different-deployment"


# --- Callback data ----------------------------------------------------------
def test_callback_round_trips() -> None:
    script_id = uuid.uuid4()
    data = build_callback(
        CallbackAction.APPROVE_PRODUCTION, secret=SECRET, entity_id=script_id, argument="3"
    )
    payload = parse_callback(data, secret=SECRET)

    assert payload is not None
    assert payload.action is CallbackAction.APPROVE_PRODUCTION
    assert payload.entity_id == script_id
    assert payload.argument == "3"


def test_callback_fits_telegrams_limit() -> None:
    data = build_callback(
        CallbackAction.REQUEST_REVISION, secret=SECRET, entity_id=uuid.uuid4(), argument="9999"
    )
    assert len(data.encode("utf-8")) <= MAX_CALLBACK_BYTES


def test_tampered_entity_is_rejected() -> None:
    """Swapping the script id must not approve a different script."""
    data = build_callback(
        CallbackAction.APPROVE_PRODUCTION, secret=SECRET, entity_id=uuid.uuid4(), argument="1"
    )
    action, _, argument, signature = data.split("|")
    forged = "|".join([action, uuid.uuid4().hex, argument, signature])
    assert parse_callback(forged, secret=SECRET) is None


def test_tampered_action_is_rejected() -> None:
    """Turning 'skip' into 'approve' must fail the signature check."""
    data = build_callback(CallbackAction.SKIP, secret=SECRET, entity_id=uuid.uuid4(), argument="1")
    _, entity, argument, signature = data.split("|")
    forged = "|".join([CallbackAction.APPROVE_PRODUCTION.value, entity, argument, signature])
    assert parse_callback(forged, secret=SECRET) is None


def test_tampered_version_is_rejected() -> None:
    data = build_callback(
        CallbackAction.APPROVE_PRODUCTION, secret=SECRET, entity_id=uuid.uuid4(), argument="1"
    )
    action, entity, _, signature = data.split("|")
    assert parse_callback("|".join([action, entity, "2", signature]), secret=SECRET) is None


def test_a_button_from_another_deployment_is_rejected() -> None:
    data = build_callback(CallbackAction.SKIP, secret=OTHER_SECRET, entity_id=uuid.uuid4())
    assert parse_callback(data, secret=SECRET) is None


@pytest.mark.parametrize("data", ["", "garbage", "ap|x|y", "ap|not-a-uuid|1|deadbeef00"])
def test_malformed_callback_data_is_rejected(data: str) -> None:
    assert parse_callback(data, secret=SECRET) is None


def test_argument_may_not_smuggle_a_separator() -> None:
    with pytest.raises(ValueError, match="separator"):
        build_callback(CallbackAction.SKIP, secret=SECRET, argument="a|b")


# --- Invite codes -----------------------------------------------------------
def test_generated_codes_are_typable_and_unique() -> None:
    codes = {generate_code() for _ in range(50)}
    assert len(codes) == 50
    assert all(len(code) == CODE_LENGTH for code in codes)
    # Ambiguous characters would be misread when dictated over a call.
    assert not (set("".join(codes)) & set("O0I1L"))


def test_codes_are_matched_after_normalisation() -> None:
    code = generate_code()
    assert codes_match(f"  {code.lower()} ", hash_code(code))
    assert normalize_code(f"{code[:5]}-{code[5:]}") == code


def test_a_wrong_code_does_not_match() -> None:
    assert not codes_match(generate_code(), hash_code(generate_code()))


def test_hash_is_not_the_code() -> None:
    code = generate_code()
    assert code not in hash_code(code)


def test_a_fresh_invite_is_valid() -> None:
    facts = InviteFacts(
        role=Role.EMPLOYEE, active=True, expires_at=utcnow() + timedelta(days=1), max_uses=1
    )
    assert check_invite(facts, now=utcnow()).valid


def test_an_expired_invite_is_refused() -> None:
    facts = InviteFacts(
        role=Role.EMPLOYEE, active=True, expires_at=utcnow() - timedelta(seconds=1), max_uses=1
    )
    verdict = check_invite(facts, now=utcnow())
    assert not verdict.valid
    assert verdict.reason is InviteRejection.EXPIRED


def test_an_exhausted_invite_is_refused() -> None:
    facts = InviteFacts(role=Role.EMPLOYEE, active=True, max_uses=2, use_count=2)
    verdict = check_invite(facts, now=utcnow())
    assert not verdict.valid
    assert verdict.reason is InviteRejection.EXHAUSTED


def test_a_disabled_invite_is_refused() -> None:
    facts = InviteFacts(role=Role.EMPLOYEE, active=False, max_uses=5)
    verdict = check_invite(facts, now=utcnow())
    assert not verdict.valid
    assert verdict.reason is InviteRejection.DISABLED


def test_an_invite_with_uses_left_is_valid() -> None:
    facts = InviteFacts(role=Role.TEAM_LEAD, active=True, max_uses=5, use_count=4)
    assert check_invite(facts, now=utcnow()).valid


# --- Who may invite whom ----------------------------------------------------
def test_owner_may_invite_every_invitable_role() -> None:
    assert can_invite_role(Role.OWNER, Role.EMPLOYEE)
    assert can_invite_role(Role.OWNER, Role.TEAM_LEAD)
    assert can_invite_role(Role.OWNER, Role.ADMIN)


def test_nobody_may_invite_an_owner() -> None:
    """OWNER is configuration, not something a code can hand out."""
    assert not can_invite_role(Role.OWNER, Role.OWNER)
    assert not can_invite_role(Role.ADMIN, Role.OWNER)


def test_a_role_may_not_invite_its_own_level_or_above() -> None:
    assert not can_invite_role(Role.ADMIN, Role.ADMIN)
    assert not can_invite_role(Role.TEAM_LEAD, Role.ADMIN)
    assert not can_invite_role(Role.EMPLOYEE, Role.EMPLOYEE)


def test_admin_may_invite_downwards() -> None:
    assert can_invite_role(Role.ADMIN, Role.TEAM_LEAD)
    assert can_invite_role(Role.ADMIN, Role.EMPLOYEE)
