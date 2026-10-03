"""Vietnamese display labels for the role enum, and the aliases that map back.

Two rules keep this module honest.

* :class:`~meobot.domain.identity.models.Role` stays the only authority. The
  database enum, the permission matrix, the audit trail and every policy
  decision are written in ``OWNER`` / ``ADMIN`` / ``TEAM_LEAD`` / ``EMPLOYEE``,
  and nothing here renames any of them.
* Everything a *person* reads is a label. ``OWNER`` is shown as "Chủ sở hữu"
  and ``EMPLOYEE`` as "Nhân viên". One table, so the wording cannot drift
  between ``/start``, ``/whoami``, a refusal from the policy engine, the web
  admin and what the assistant says in a sentence.

  ``OWNER`` is **workspace ownership**, not a job title. It used to be shown as
  "Trưởng phòng", which is what the department calls its head - and the head
  happens to hold the owner account today. The two are different facts: the
  approval gate is still "Duyệt Trưởng phòng", the notifications still say a
  request was approved by the Trưởng phòng, and none of that names a role.

Aliases run the other way. A command argument or an LLM tool call may say
"member", "TRUONG_NHOM" or "quản trị viên"; :func:`parse_role` folds it back to
the enum *before* the policy engine is asked anything, so an alias can never be
a way around a permission check - it resolves to the same ``Role`` the raw enum
name would have resolved to, or to nothing at all.

Matching is exact on the whole argument, never a search inside a sentence.
A role is granted by somebody who holds the authority to grant it; it is never
inferred from a job title, a signature, or the words in a chat message. That is
why :func:`parse_role` returns ``None`` for "chị ấy là trưởng phòng marketing"
even though the label is in there.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Sequence
from typing import Annotated, Any

from pydantic import BeforeValidator

from meobot.domain.identity.models import Actor, Role

#: What each authoritative role is called, in Vietnamese - on Telegram, on the
#: web (sent as ``role_label``) and in the audit trail. The one source.
ROLE_LABELS: dict[Role, str] = {
    Role.OWNER: "Chủ sở hữu",
    Role.ADMIN: "Quản trị viên",
    Role.TEAM_LEAD: "Trưởng nhóm",
    Role.EMPLOYEE: "Nhân viên",
}

#: Shown for somebody who has no membership in the system but is being let in
#: temporarily. It is a *label*, not a role: there is no ``Role.GUEST``, no row
#: in the permission matrix and no way to parse it back into authority.
GUEST_LABEL = "Guest"

#: Accepted spellings per role. The display label itself is added below, so
#: "Chủ sở hữu" typed back at MeoBot resolves to ``OWNER``. "Trưởng phòng" is
#: kept as an *input* alias only - somebody who has typed it for two releases
#: still gets the same answer - and is never printed for the role.
ROLE_ALIASES: dict[Role, tuple[str, ...]] = {
    Role.OWNER: ("OWNER", "TRUONG_PHONG", "trưởng phòng", "chủ hệ thống", "chủ sở hữu"),
    Role.ADMIN: ("ADMIN", "quản trị viên"),
    Role.TEAM_LEAD: ("TEAM_LEAD", "TRUONG_NHOM", "trưởng nhóm", "team lead"),
    Role.EMPLOYEE: ("EMPLOYEE", "MEMBER", "member", "nhân viên"),
}


def fold(text: str) -> str:
    """Normalise one written form: case, diacritics, separators, spacing.

    ``"TRUONG_PHONG"``, ``"Trưởng Phòng"`` and ``"  trưởng-phòng "`` all fold to
    ``"truong phong"``, which is what makes the alias table small enough to read
    and impossible to disagree with itself.
    """
    lowered = text.strip().casefold().replace("đ", "d")
    without_marks = "".join(
        char for char in unicodedata.normalize("NFD", lowered) if not unicodedata.combining(char)
    )
    return " ".join(without_marks.replace("_", " ").replace("-", " ").split())


def _build_index() -> dict[str, Role]:
    """Fold every alias and every label into one lookup, rejecting collisions."""
    index: dict[str, Role] = {}
    for role, aliases in ROLE_ALIASES.items():
        for alias in (*aliases, ROLE_LABELS[role], role.value):
            key = fold(alias)
            existing = index.get(key)
            if existing is not None and existing is not role:
                # A collision would silently hand out the wrong authority.
                raise ValueError(f"alias {alias!r} maps to both {existing} and {role}")
            index[key] = role
    return index


_ALIAS_INDEX: dict[str, Role] = _build_index()

#: Longest alias in words ("chủ hệ thống"), so a parser knows how far to look.
MAX_ALIAS_WORDS: int = max(len(key.split()) for key in _ALIAS_INDEX)


def role_label(role: Role) -> str:
    """The Vietnamese label for an authoritative role."""
    return ROLE_LABELS[role]


def actor_label(actor: Actor) -> str:
    """The label to show for one actor - :data:`GUEST_LABEL` when unaffiliated.

    Authority is unaffected: a guest still carries whatever ``Role`` the actor
    was resolved with, and the policy engine still reads that role. Only the
    wording changes.
    """
    return GUEST_LABEL if actor.is_guest else role_label(actor.role)


def role_labels(roles: Iterable[Role]) -> str:
    """Comma-separated labels, least privileged first - for "Chọn: ..." hints."""
    return ", ".join(role_label(role) for role in sorted(roles, key=lambda item: item.rank))


def parse_role(text: str | None) -> Role | None:
    """Resolve one written role to the authoritative enum, or ``None``.

    The whole string must be an alias. Nothing is inferred from a longer phrase:
    ``parse_role("trưởng phòng marketing")`` is ``None``, deliberately.
    """
    if text is None:
        return None
    return _ALIAS_INDEX.get(fold(text))


def parse_role_prefix(tokens: Sequence[str]) -> tuple[Role | None, list[str]]:
    """Take a role off the front of ``tokens``; return it and what is left.

    Labels are several words long ("trưởng nhóm", "team lead"), so a command
    like ``/add_user 42 trưởng nhóm Nguyễn Văn A`` cannot just split on spaces.
    The longest match wins, which keeps the remainder - a person's name - intact.
    """
    for size in range(min(MAX_ALIAS_WORDS, len(tokens)), 0, -1):
        role = parse_role(" ".join(tokens[:size]))
        if role is not None:
            return role, list(tokens[size:])
    return None, list(tokens)


def coerce_role(value: Any) -> Any:
    """Pydantic ``BeforeValidator``: fold an alias, leave anything else alone.

    Applied wherever a role arrives from outside the codebase - an LLM tool
    call, an API body - so ``"MEMBER"`` becomes ``Role.EMPLOYEE`` before
    validation, and before any permission is evaluated against it.
    """
    if isinstance(value, Role):
        return value
    if isinstance(value, str):
        return parse_role(value) or value
    return value


#: A ``Role`` field that accepts every alias in :data:`ROLE_ALIASES`.
RoleInput = Annotated[Role, BeforeValidator(coerce_role)]
