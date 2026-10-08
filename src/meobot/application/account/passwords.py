"""Password hashing, the new-password rules and temporary passwords. No I/O.

The hash
--------

``hashlib.scrypt`` from the standard library - memory-hard, no new dependency -
with ``n=2**14, r=8, p=1``, a 16-byte random salt and a 64-byte key, stored as::

    scrypt$16384$8$1$<salt base64>$<hash base64>

The parameters travel with the hash, so they can be raised later without
invalidating anybody's password. Verification recomputes the key and compares
with :func:`hmac.compare_digest`. The password itself is never stored, logged
or audited - not even the default one, which lives only in settings.

Passwords are NFKC-normalised before hashing: the same Vietnamese word typed on
a Mac (decomposed) and on Windows (composed) must be the same password.

Timing
------

A login for an unknown id, or for an account still on the default password,
runs :func:`burn_dummy_hash` so that every attempt costs exactly one scrypt.
Without it "unknown id" would answer in microseconds and "known id" in tens of
milliseconds, which is a user-enumeration oracle.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import secrets
import unicodedata
from functools import lru_cache

from meobot.domain.account.errors import PasswordRejectedError

SCHEME = "scrypt"
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SALT_BYTES = 16
KEY_BYTES = 64
#: Generous headroom over the ~16 MiB that n=2**14, r=8 needs; the stdlib
#: default (32 MiB) is close enough to the edge to fail on some builds.
_MAXMEM = 64 * 1024 * 1024

#: Anything longer than this is never a password anybody chose; it is refused
#: without being hashed in full, so a megabyte body cannot buy a megabyte of
#: hashing. It is also the only length rule a new password has.
MAX_INPUT_LENGTH = 1024
MAX_PASSWORD_LENGTH = MAX_INPUT_LENGTH

#: Temporary passwords (password reset): letters and digits without the ones
#: that read alike on a phone screen (0/O/o, 1/l/I).
TEMPORARY_PASSWORD_LENGTH = 10
TEMPORARY_PASSWORD_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"  # noqa: S105


def _normalise(password: str) -> bytes:
    return unicodedata.normalize("NFKC", password).encode("utf-8")


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _scrypt(password: str, salt: bytes, *, n: int, r: int, p: int, dklen: int) -> bytes:
    return hashlib.scrypt(
        _normalise(password), salt=salt, n=n, r=r, p=p, dklen=dklen, maxmem=_MAXMEM
    )


def hash_password(password: str) -> str:
    """The stored form of ``password``. A fresh salt every time."""
    salt = secrets.token_bytes(SALT_BYTES)
    key = _scrypt(password, salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=KEY_BYTES)
    return f"{SCHEME}${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(key)}"


def _parse(stored: str) -> tuple[int, int, int, bytes, bytes] | None:
    """The parameters, salt and key of a stored hash, or ``None`` if malformed.

    Bounded so that a corrupted row cannot make one login cost seconds.
    """
    parts = stored.split("$")
    if len(parts) != 6 or parts[0] != SCHEME:
        return None
    try:
        n, r, p = int(parts[1]), int(parts[2]), int(parts[3])
        salt = base64.b64decode(parts[4], validate=True)
        key = base64.b64decode(parts[5], validate=True)
    except (ValueError, binascii.Error):
        return None
    if not (2**10 <= n <= 2**17 and n & (n - 1) == 0 and 1 <= r <= 16 and 1 <= p <= 4):
        return None
    if len(salt) < 8 or not 16 <= len(key) <= 128:
        return None
    return n, r, p, salt, key


def verify_password(password: str, stored: str) -> bool:
    """Whether ``password`` produces ``stored``. Constant-time comparison."""
    parsed = _parse(stored)
    if parsed is None or len(password) > MAX_INPUT_LENGTH:
        # Still pay for one hash, so a malformed row is not a timing signal.
        burn_dummy_hash(password)
        return False
    n, r, p, salt, expected = parsed
    candidate = _scrypt(password, salt, n=n, r=r, p=p, dklen=len(expected))
    return hmac.compare_digest(candidate, expected)


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    """A hash of a random secret nobody knows, made once per process."""
    return hash_password(secrets.token_urlsafe(24))


def burn_dummy_hash(password: str) -> None:
    """Spend exactly what one verification costs, and learn nothing."""
    parsed = _parse(_dummy_hash())
    assert parsed is not None
    n, r, p, salt, expected = parsed
    candidate = _scrypt(password[:MAX_INPUT_LENGTH], salt, n=n, r=r, p=p, dklen=len(expected))
    hmac.compare_digest(candidate, expected)


def matches_default(password: str, default: str) -> bool:
    """Whether ``password`` is the default one. Constant time in both lengths.

    Digests of equal length are compared rather than the strings, so how long
    the comparison takes says nothing about how long either input is.
    """
    return len(password) <= MAX_INPUT_LENGTH and hmac.compare_digest(
        hashlib.sha256(_normalise(password)).digest(),
        hashlib.sha256(_normalise(default)).digest(),
    )


def check_new_password(new_password: str, *, default: str) -> None:
    """The rules a new password must satisfy before anything is hashed.

    Deliberately few (product decision): a person may choose any password at
    all, except an empty one and the shared default - the forced change after
    signing in with the default would mean nothing if the default could be
    chosen again. Length is capped only so one hash stays bounded in cost.

    Raises:
        PasswordRejectedError: ``password_empty`` (empty or only spaces),
            ``password_too_long`` (over :data:`MAX_PASSWORD_LENGTH`) or
            ``password_is_default``.
    """
    if not new_password.strip():
        raise PasswordRejectedError(
            "password_empty", "Mật khẩu mới không được để trống.", field="new_password"
        )
    if len(new_password) > MAX_PASSWORD_LENGTH:
        raise PasswordRejectedError(
            "password_too_long",
            f"Mật khẩu mới dài tối đa {MAX_PASSWORD_LENGTH} ký tự.",
            field="new_password",
        )
    if matches_default(new_password, default):
        raise PasswordRejectedError(
            "password_is_default",
            "Mật khẩu mới không được trùng mật khẩu mặc định.",
            field="new_password",
        )


def generate_temporary_password() -> str:
    """A fresh random temporary password: 10 characters, letters and digits.

    Drawn with :mod:`secrets` from :data:`TEMPORARY_PASSWORD_ALPHABET`, and
    redrawn until it has at least one letter and one digit so it never looks
    like a number or a word. About 57 bits.
    """
    while True:
        candidate = "".join(
            secrets.choice(TEMPORARY_PASSWORD_ALPHABET) for _ in range(TEMPORARY_PASSWORD_LENGTH)
        )
        if any(char.isdigit() for char in candidate) and any(char.isalpha() for char in candidate):
            return candidate


async def hash_password_async(password: str) -> str:
    """:func:`hash_password` off the event loop - one hash is tens of ms."""
    return await asyncio.to_thread(hash_password, password)


async def verify_password_async(password: str, stored: str) -> bool:
    return await asyncio.to_thread(verify_password, password, stored)


async def burn_dummy_hash_async(password: str) -> None:
    await asyncio.to_thread(burn_dummy_hash, password)


__all__ = [
    "MAX_INPUT_LENGTH",
    "MAX_PASSWORD_LENGTH",
    "TEMPORARY_PASSWORD_ALPHABET",
    "TEMPORARY_PASSWORD_LENGTH",
    "burn_dummy_hash",
    "burn_dummy_hash_async",
    "check_new_password",
    "generate_temporary_password",
    "hash_password",
    "hash_password_async",
    "matches_default",
    "verify_password",
    "verify_password_async",
]
