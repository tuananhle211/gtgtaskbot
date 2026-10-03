"""Encrypting a secret that has to be stored in the database and read back.

Step 1F.2.4b. MeoBot had nothing like this before, and the reason it had
nothing is worth stating: until now **no secret needed to be recoverable**.

Every third-party credential the system holds is supplied by the environment -
``TELEGRAM_BOT_TOKEN``, ``LLM_API_KEY``, ``META_APP_SECRET`` - or is a service
account file on disk. The one credential that *is* in the database,
``web_sessions.token_hash``, is hashed, and its own docstring says why that is
the right answer there: *"there is no key to lose"*.

A YouTube refresh token is the first secret that breaks that pattern. It varies
per row, it is issued by somebody else, and it must be recoverable in plaintext
months later or unattended sync cannot work at all. Hashing it is not an option
and neither is storing it as it came.

The construction
----------------

AES-256-GCM, which is authenticated encryption: a ciphertext that has been
altered fails to decrypt rather than decrypting to something else. Per value:

* a fresh 96-bit nonce from :func:`os.urandom`. Never reused, never derived from
  the plaintext - GCM's one catastrophic failure mode is a repeated
  ``(key, nonce)`` pair;
* **additional authenticated data** binding the ciphertext to the row it belongs
  to. The AAD is not encrypted; it is authenticated, so a ciphertext lifted out
  of one connection row and pasted into another fails to decrypt instead of
  quietly handing that channel somebody else's YouTube account;
* a ``v1`` scheme tag and a **key id**, so a second key can be introduced and
  old values keep decrypting while new ones use the new key. Rotation is a
  configuration change, not a migration.

The stored form is ASCII and self-describing::

    v1:<key_id>:<base64 nonce>:<base64 ciphertext+tag>

Where the key comes from
------------------------

``PR_SECRET_ENCRYPTION_KEY`` - 32 bytes, base64 - or
``PR_SECRET_ENCRYPTION_KEY_FILE`` pointing at a file containing the same, which
is how the deployment already delivers the Google service account: a host file
mounted read-only at ``/run/secrets`` into the services that need it and no
others. See ``x-secrets`` in ``docker-compose.yml``.

Additional keys for rotation are supplied as
``PR_SECRET_ENCRYPTION_KEYS_OLD`` - ``<key_id>:<base64 key>`` entries, comma
separated - and are used for decryption only.

Not configured is a state, not a crash
---------------------------------------

A deployment with no key configured starts normally. Every other part of MeoBot
works; only the YouTube connector reports that it is not configured, which is
:class:`SecretBoxNotConfiguredError` becoming *"Cấu hình YouTube chưa sẵn sàng"*
at the API boundary. Refusing to boot would take the bot and the whole PR module
down over a feature nobody on that deployment is using.

What this module will not do
-----------------------------

It does not log, and it never puts plaintext, ciphertext or key material into an
exception message. A failure says which key id was wanted and nothing else -
enough to diagnose a rotation mistake, useless to anybody reading a log.
"""

from __future__ import annotations

import base64
import binascii
import os
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from meobot.core.errors import ConfigurationError, MeoBotError

#: The only scheme this module writes. A stored value that does not start with
#: it was written by something else, and is refused rather than guessed at.
SCHEME = "v1"

#: AES-256. Anything else is a configuration mistake, not a shorter key.
KEY_BYTES = 32

#: 96 bits, which is the nonce size AES-GCM is specified and optimised for.
NONCE_BYTES = 12

#: What an unnamed primary key is called, so a deployment that never rotates
#: does not have to invent an id.
DEFAULT_KEY_ID = "primary"


class SecretBoxNotConfiguredError(ConfigurationError):
    """No encryption key is configured on this deployment.

    A :class:`~meobot.core.errors.ConfigurationError` rather than an internal
    failure: nothing is broken, a feature is simply not set up, and the caller's
    job is to say so in Vietnamese rather than to report an error.
    """


class SecretDecryptionError(MeoBotError):
    """A stored secret could not be read back.

    A wrong key, a rotated-away key id, a truncated column, or a value moved
    between rows so its AAD no longer matches. Deliberately one exception for
    all of them: telling a caller *which* would describe the key material.
    """

    code = "secret.undecryptable"


@dataclass(frozen=True, slots=True)
class SecretBox:
    """Encrypts and decrypts values for one deployment.

    Args:
        keys: ``key_id`` -> 32 raw bytes. Every key here can decrypt.
        primary_key_id: The key new values are written with. Must be in
            ``keys``.
    """

    keys: dict[str, bytes]
    primary_key_id: str

    def encrypt(self, plaintext: str, *, aad: str) -> str:
        """Encrypt ``plaintext``, bound to ``aad``.

        Args:
            plaintext: The secret. Never logged by this module.
            aad: What this ciphertext belongs to - for a channel connection,
                its id. The same string must be supplied to :meth:`decrypt`, so
                a value copied to another row cannot be read there.

        Returns:
            ``v1:<key_id>:<nonce>:<ciphertext>``, ASCII, safe to store.
        """
        nonce = os.urandom(NONCE_BYTES)
        key = self.keys[self.primary_key_id]
        sealed = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), aad.encode("utf-8"))
        return ":".join(
            (
                SCHEME,
                self.primary_key_id,
                base64.b64encode(nonce).decode("ascii"),
                base64.b64encode(sealed).decode("ascii"),
            )
        )

    def decrypt(self, envelope: str, *, aad: str) -> str:
        """Read a value back, or refuse.

        Raises:
            SecretDecryptionError: Malformed, written under a key this
                deployment no longer has, or authenticating against different
                ``aad`` than it was sealed with.
        """
        parts = (envelope or "").split(":")
        if len(parts) != 4 or parts[0] != SCHEME:
            raise SecretDecryptionError("Stored secret is not in a recognised format")
        _, key_id, nonce_b64, sealed_b64 = parts
        key = self.keys.get(key_id)
        if key is None:
            raise SecretDecryptionError(
                "Stored secret was written with a key this deployment does not have",
                details={"key_id": key_id},
            )
        try:
            nonce = base64.b64decode(nonce_b64, validate=True)
            sealed = base64.b64decode(sealed_b64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise SecretDecryptionError("Stored secret is not valid base64") from exc
        try:
            return AESGCM(key).decrypt(nonce, sealed, aad.encode("utf-8")).decode("utf-8")
        except (InvalidTag, ValueError) as exc:
            # One message for every failure. Which of them it was is information
            # about the key, and a caller has nothing useful to do with it.
            raise SecretDecryptionError("Stored secret could not be decrypted") from exc


def _decode_key(material: str, *, name: str) -> bytes:
    try:
        key = base64.b64decode(material.strip(), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ConfigurationError(f"{name} is not valid base64", details={"setting": name}) from exc
    if len(key) != KEY_BYTES:
        raise ConfigurationError(
            f"{name} must decode to {KEY_BYTES} bytes",
            details={"setting": name, "decoded_bytes": len(key)},
        )
    return key


def _read_key_file(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ConfigurationError(
            "PR_SECRET_ENCRYPTION_KEY_FILE cannot be read",
            details={"setting": "PR_SECRET_ENCRYPTION_KEY_FILE"},
        ) from exc


def _parse_old_keys(raw: str) -> dict[str, bytes]:
    """``id:key,id:key`` -> keys that may decrypt but never encrypt."""
    keys: dict[str, bytes] = {}
    for entry in raw.split(","):
        item = entry.strip()
        if not item:
            continue
        key_id, separator, material = item.partition(":")
        if not separator or not key_id.strip():
            raise ConfigurationError(
                "PR_SECRET_ENCRYPTION_KEYS_OLD entries must be <key_id>:<base64 key>",
                details={"setting": "PR_SECRET_ENCRYPTION_KEYS_OLD"},
            )
        keys[key_id.strip()] = _decode_key(material, name="PR_SECRET_ENCRYPTION_KEYS_OLD")
    return keys


def build_secret_box(settings: object) -> SecretBox:
    """The deployment's secret box, or a refusal naming the missing setting.

    Args:
        settings: The application :class:`~meobot.core.config.Settings`. Typed
            loosely to keep ``core.config`` free of an import back into here.

    Raises:
        SecretBoxNotConfiguredError: No key is configured. The caller turns this
            into "chưa sẵn sàng", never into a 500.
        ConfigurationError: A key is configured and is unusable.
    """
    primary_id = str(getattr(settings, "pr_secret_encryption_key_id", "") or DEFAULT_KEY_ID)

    material: str | None = None
    secret = getattr(settings, "pr_secret_encryption_key", None)
    if secret is not None:
        material = secret.get_secret_value()
    elif getattr(settings, "pr_secret_encryption_key_file", None):
        material = _read_key_file(str(settings.pr_secret_encryption_key_file))  # type: ignore[attr-defined]

    if not material:
        raise SecretBoxNotConfiguredError(
            "PR secret encryption is not configured",
            details={"setting": "PR_SECRET_ENCRYPTION_KEY"},
        )

    keys = {primary_id: _decode_key(material, name="PR_SECRET_ENCRYPTION_KEY")}
    old = getattr(settings, "pr_secret_encryption_keys_old", None)
    if old is not None:
        # Retired keys never overwrite the primary: a rotation that reused an id
        # would otherwise silently make the old key the writing key.
        for key_id, key in _parse_old_keys(old.get_secret_value()).items():
            keys.setdefault(key_id, key)

    return SecretBox(keys=keys, primary_key_id=primary_id)


def generate_key() -> str:
    """A fresh base64 key, for an operator setting a deployment up.

    Here rather than in a script so that the size and the encoding are stated
    once, next to the code that validates them.
    """
    return base64.b64encode(os.urandom(KEY_BYTES)).decode("ascii")


__all__: list[str] = [
    "DEFAULT_KEY_ID",
    "KEY_BYTES",
    "NONCE_BYTES",
    "SCHEME",
    "SecretBox",
    "SecretBoxNotConfiguredError",
    "SecretDecryptionError",
    "build_secret_box",
    "generate_key",
]
