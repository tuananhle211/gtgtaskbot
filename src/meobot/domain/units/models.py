"""Units and membership - the wall between PR and Ads.

A **unit** is a department that orders and tracks video work. Two exist: PR,
which runs the content workflow that predates units, and Ads, which runs the
order engine in :mod:`meobot.domain.orders`. A person is **tagged** into one or
both; what they can see is decided by the tag, what they can do inside a unit
by their role in it. The two never mix: an Ads member sees no PR content, a PR
member sees no Ads order, and only the OWNER sees both.

The unit is deliberately **not** a field on :class:`~meobot.domain.identity.models.Actor`.
The actor is rebuilt from the ``users`` row on every request and every Telegram
update, and hundreds of call sites construct one directly; membership is a
per-request lookup instead, resolved once and shared by the gate and the route.

No tag, no unit
---------------

A user with **no** open membership row belongs to no unit: they see neither
stream until a team lead (or an ADMIN/OWNER) tags them. Joining by invite
creates no tag. Migration ``0042`` tagged every account that existed before
units did as PR, and ``0048`` tagged every active account still untagged when
the rule changed, so nobody working in PR lost access when it did.

The OWNER and the ADMIN see every unit whatever their tags.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class UnitCode(StrEnum):
    """The two units. Stored in ``org_units.code``."""

    PR = "PR"
    ADS = "ADS"


#: The namespace migration ``0042`` derives the seeded unit ids from. Kept
#: here as well so code that must name a unit before it has read the row - the
#: task mirror's PR rows in :mod:`meobot.application.tasks.sync` - names the
#: same id the migration wrote. A parity test holds the two copies equal.
UNIT_SEED_NAMESPACE = uuid.UUID("5a9f3b0e-6c1d-4f8a-9b2e-004200420042")


def unit_seed_id(code: UnitCode) -> uuid.UUID:
    """The id ``0042`` gave this unit's row."""
    return uuid.uuid5(UNIT_SEED_NAMESPACE, f"unit:{code.value}")


class UnitMemberRole(StrEnum):
    """What a person does inside a unit.

    ``MEMBER`` is the only PR role: PR authority still comes from
    :class:`~meobot.domain.identity.models.Role` and the capability grants, and
    the tag only says "this person is PR". The other values are Ads functions:
    the ordering side (``ORDERER``), the department head who approves orders
    and the final cut (``HEAD``), and one role per production function. A
    function's **Leader** is a member of that function with ``is_lead`` set.
    """

    MEMBER = "MEMBER"
    ORDERER = "ORDERER"
    HEAD = "HEAD"
    BIEN_TAP = "BIEN_TAP"
    THIET_KE = "THIET_KE"
    DUNG = "DUNG"


#: The Ads roles that correspond one-to-one with a production node type.
FUNCTION_ROLES: frozenset[UnitMemberRole] = frozenset(
    {UnitMemberRole.BIEN_TAP, UnitMemberRole.THIET_KE, UnitMemberRole.DUNG}
)


class UnitSettings(BaseModel):
    """The per-unit knobs, stored as JSON in ``org_units.settings``.

    Kept out of :class:`~meobot.core.config.Settings` on purpose: these are
    the department's choices, edited on the admin page, not deployment
    configuration.
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    #: An order older than this many days that is not finished is flagged
    #: urgent ("Gấp") on every list. The Apexmed brief says seven.
    urgent_days: int = Field(default=7, ge=1, le=365)
    #: Links to the shared storage, shown as buttons on the dashboard.
    media_nas_url: str | None = None
    design_nas_url: str | None = None
    #: Who attaches the product link on a full-pipeline (BTD) order once the
    #: editor's cut is approved: the editor (``DUNG``) or the script lead
    #: (``BIEN_TAP``). The brief left this open; the editor is the default.
    #: Every other process hands the link to its last production node.
    btd_link_attacher: UnitMemberRole = UnitMemberRole.DUNG
    #: Whether order notifications are also sent to Telegram. The web inbox is
    #: always written; Telegram is opt-in per unit.
    telegram_enabled: bool = False
    #: Whether a node's Leader reviews the work before the next node starts.
    #: Off for script and design for now (the brief: "tạm thời bỏ duyệt"):
    #: handing the work in finishes the node and the next one starts at once.
    review_bien_tap: bool = False
    review_thiet_ke: bool = False
    review_dung: bool = True
    #: Whether the script lead watches the finished cut of an order whose
    #: process has a script node before the orderer's final review. Off with
    #: the script review above.
    review_video_by_script_lead: bool = False
    #: The Ads permission matrix, role -> permission -> NONE/OWN/ALL. Only what
    #: differs from :data:`~meobot.domain.orders.permissions.DEFAULT_MATRIX`
    #: needs storing; validated by the admin service.
    permissions: dict[str, dict[str, str]] = Field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class UnitMembershipEntry:
    """One active tag: a person's standing in one unit."""

    unit_id: uuid.UUID
    unit_code: UnitCode
    role: UnitMemberRole
    is_lead: bool = False
    member_code: str | None = None
    personal_nas_url: str | None = None


@dataclass(frozen=True, slots=True)
class UnitMembership:
    """Everything the gate and the scopes need to know about one actor.

    ``is_owner`` and ``is_admin`` cross the wall: the OWNER and the ADMIN see
    every unit whatever their tags (the OWNER also acts as the Ads head). Inside
    Ads an ADMIN holds the permission matrix's Admin column. Everybody else sees
    exactly the units they are tagged into - none when untagged.
    """

    user_id: uuid.UUID | None
    entries: tuple[UnitMembershipEntry, ...] = field(default_factory=tuple)
    is_owner: bool = False
    #: ``Role.ADMIN``: sees every unit; the matrix's Admin column inside Ads.
    is_admin: bool = False

    @property
    def sees_all(self) -> bool:
        """OWNER or ADMIN: every unit, and the "all units" view."""
        return self.is_owner or self.is_admin

    @property
    def is_untagged(self) -> bool:
        """No open tag at all (an OWNER/ADMIN still sees every unit)."""
        return not self.entries

    def has(self, code: UnitCode) -> bool:
        """Whether this actor may see the unit at all."""
        return self.sees_all or any(entry.unit_code is code for entry in self.entries)

    def entry(self, code: UnitCode) -> UnitMembershipEntry | None:
        """The actor's tag in one unit, or ``None`` when not tagged."""
        for entry in self.entries:
            if entry.unit_code is code:
                return entry
        return None

    def visible_units(self) -> tuple[UnitCode, ...]:
        """The units this actor may switch between, in a stable order."""
        if self.sees_all:
            return tuple(UnitCode)
        return tuple(code for code in UnitCode if self.has(code))


__all__ = [
    "FUNCTION_ROLES",
    "UNIT_SEED_NAMESPACE",
    "UnitCode",
    "UnitMemberRole",
    "UnitMembership",
    "UnitMembershipEntry",
    "UnitSettings",
    "unit_seed_id",
]
