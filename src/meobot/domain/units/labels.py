"""The Vietnamese words for units and unit roles. One vocabulary, server-owned.

The units are shown as **streams** ("Luồng"): PR is "Luồng PR", ADS is
"Luồng Order (ORD)". The codes stay ``PR`` / ``ADS`` everywhere a machine reads
them; only what a person reads changes. A chip shows the short tag
(:func:`unit_short_label`): "PR" / "ORD".
"""

from __future__ import annotations

from meobot.domain.units.models import UnitCode, UnitMemberRole

UNIT_LABELS: dict[UnitCode, str] = {
    UnitCode.PR: "Luồng PR",
    UnitCode.ADS: "Luồng Order (ORD)",
}

#: The tag on a chip, next to a name.
UNIT_SHORT_LABELS: dict[UnitCode, str] = {
    UnitCode.PR: "PR",
    UnitCode.ADS: "ORD",
}

UNIT_ROLE_LABELS: dict[UnitMemberRole, str] = {
    UnitMemberRole.MEMBER: "Thành viên",
    UnitMemberRole.ORDERER: "Marketing (người order)",
    UnitMemberRole.HEAD: "Trưởng phòng ORD",
    UnitMemberRole.BIEN_TAP: "Biên tập",
    UnitMemberRole.THIET_KE: "Thiết kế",
    UnitMemberRole.DUNG: "Dựng",
}

#: The department tag of an ORD function role, shown as a chip beside the
#: stream tag: [ORD][BT].
FUNCTION_TAGS: dict[UnitMemberRole, str] = {
    UnitMemberRole.BIEN_TAP: "BT",
    UnitMemberRole.THIET_KE: "TK",
    UnitMemberRole.DUNG: "D",
}


def unit_label(code: UnitCode) -> str:
    return UNIT_LABELS[code]


def unit_short_label(code: UnitCode) -> str:
    return UNIT_SHORT_LABELS[code]


def function_tag(code: UnitCode, role: UnitMemberRole) -> str | None:
    """``"BT"`` / ``"TK"`` / ``"D"`` for an ORD function role, else ``None``."""
    if code is not UnitCode.ADS:
        return None
    return FUNCTION_TAGS.get(role)


#: A function role with ``is_lead`` is that function's head: the person every
#: node of the function is routed to first, to hand out to their team.
LEAD_ROLE_LABELS: dict[UnitMemberRole, str] = {
    UnitMemberRole.BIEN_TAP: "Trưởng phòng Biên kịch",
    UnitMemberRole.THIET_KE: "Trưởng phòng Design",
    UnitMemberRole.DUNG: "Trưởng phòng Dựng",
}


def unit_role_label(role: UnitMemberRole, is_lead: bool = False) -> str:
    if is_lead and role in LEAD_ROLE_LABELS:
        return LEAD_ROLE_LABELS[role]
    return UNIT_ROLE_LABELS[role]


__all__ = [
    "FUNCTION_TAGS",
    "LEAD_ROLE_LABELS",
    "UNIT_LABELS",
    "UNIT_ROLE_LABELS",
    "UNIT_SHORT_LABELS",
    "function_tag",
    "unit_label",
    "unit_role_label",
    "unit_short_label",
]
