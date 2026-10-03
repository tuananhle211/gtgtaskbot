"""Invite-code rules.

An invite code is a bearer secret: whoever types it gets the role it carries.
It is therefore stored *hashed*, never in plaintext, and every acceptance path
runs through :func:`check_invite`, which reports one machine-readable reason
for a refusal.

Pure domain: hashing and expiry arithmetic only, no database access.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import string
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from meobot.domain.identity.models import Role

#: Ambiguous characters (O/0, I/1/L) are excluded so a code survives being
#: read out loud or retyped from a screenshot.
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 10

#: Roles an invite may grant. OWNER is never invitable - it is configuration.
INVITABLE_ROLES: frozenset[Role] = frozenset({Role.EMPLOYEE, Role.TEAM_LEAD, Role.ADMIN})


class InviteRejection(StrEnum):
    """Why an invite code was refused."""

    UNKNOWN = "unknown_code"
    DISABLED = "disabled"
    EXPIRED = "expired"
    EXHAUSTED = "exhausted"
    ALREADY_REGISTERED = "already_registered"


class InviteCheck(BaseModel):
    """Verdict on one redemption attempt."""

    model_config = ConfigDict(frozen=True)

    valid: bool
    reason: InviteRejection | None = None


class InviteFacts(BaseModel):
    """The stored state of an invite, as the domain needs to see it."""

    model_config = ConfigDict(frozen=True)

    role: Role
    active: bool
    expires_at: datetime | None = None
    max_uses: int = Field(default=1, ge=1)
    use_count: int = Field(default=0, ge=0)


def generate_code() -> str:
    """Generate a fresh, human-typable invite code."""
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(CODE_LENGTH))


def normalize_code(code: str) -> str:
    """Fold the shapes humans type: spaces, dashes, lower case."""
    return "".join(
        char for char in code.strip().upper() if char in string.ascii_uppercase + string.digits
    )


def hash_code(code: str) -> str:
    """One-way hash of a normalised code.

    SHA-256 without a work factor is deliberate and sufficient here: codes are
    high-entropy random strings, not user-chosen passwords, so there is nothing
    for a dictionary attack to guess.
    """
    return hashlib.sha256(normalize_code(code).encode("utf-8")).hexdigest()


def codes_match(code: str, stored_hash: str) -> bool:
    """Constant-time comparison of a typed code against a stored hash."""
    return hmac.compare_digest(hash_code(code), stored_hash)


def check_invite(facts: InviteFacts, *, now: datetime) -> InviteCheck:
    """Decide whether an invite may be redeemed at ``now`` (UTC)."""
    if not facts.active:
        return InviteCheck(valid=False, reason=InviteRejection.DISABLED)
    if facts.expires_at is not None and now >= facts.expires_at:
        return InviteCheck(valid=False, reason=InviteRejection.EXPIRED)
    if facts.use_count >= facts.max_uses:
        return InviteCheck(valid=False, reason=InviteRejection.EXHAUSTED)
    return InviteCheck(valid=True)


def can_invite_role(actor_role: Role, target_role: Role) -> bool:
    """True when ``actor_role`` may hand out ``target_role``.

    A creator can never invite somebody at or above their own level, which
    stops an ADMIN from minting a second OWNER-equivalent account.
    """
    if target_role not in INVITABLE_ROLES:
        return False
    return actor_role.outranks(target_role)
