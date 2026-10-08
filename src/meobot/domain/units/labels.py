"""The Vietnamese words for units and unit roles. One vocabulary, server-owned."""

from __future__ import annotations

from meobot.domain.units.models import UnitCode, UnitMemberRole

UNIT_LABELS: dict[UnitCode, str] = {
    UnitCode.PR: "Phòng PR",
    UnitCode.ADS: "Phòng Ads",
}

UNIT_ROLE_LABELS: dict[UnitMemberRole, str] = {
    UnitMemberRole.MEMBER: "Thành viên",
    UnitMemberRole.ORDERER: "Marketing (người order)",
    UnitMemberRole.HEAD: "Trưởng phòng Ads",
    UnitMemberRole.BIEN_TAP: "Biên tập",
    UnitMemberRole.THIET_KE: "Thiết kế",
    UnitMemberRole.DUNG: "Dựng",
}


def unit_label(code: UnitCode) -> str:
    return UNIT_LABELS[code]


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


__all__ = ["LEAD_ROLE_LABELS", "UNIT_LABELS", "UNIT_ROLE_LABELS", "unit_label", "unit_role_label"]
