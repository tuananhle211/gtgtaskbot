"""Who sees Phòng Ads (chốt 24/10: the ADS department; 02/11: + a tick).

* the OWNER / an ADMIN: everything, and the OWNER the "Kết nối BM" screen;
* a HEAD of the ``ADS`` department: every marketer's numbers, and assigns ad
  accounts to people;
* a MEMBER of it: their own numbers;
* whoever holds ``ADS DASHBOARD`` (``domain/ads/permissions.py``, ticked by
  the OWNER per person or per department): every marketer's numbers, read
  only - a member of the department keeps their own rights on top;
* anybody else: the module does not exist for them (404).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from meobot.application.org.access import Access
from meobot.core.errors import NotFoundError
from meobot.domain.ads.models import ADS_DEPARTMENT_CODE
from meobot.domain.ads.permissions import MODULE_ADS, AdsDepartmentPermission
from meobot.domain.org.models import Position
from meobot.domain.units.models import UnitMembership


class AdsNotVisibleError(NotFoundError):
    code = "ads_not_visible"

    def __init__(self) -> None:
        super().__init__("Không tìm thấy.", details={"reason": "ads_not_visible"})


@dataclass(frozen=True, slots=True)
class AdsAccess:
    user_id: uuid.UUID | None
    view: bool = False
    #: Every marketer's numbers (else only ``user_id``'s).
    view_all: bool = False
    #: Assign ad accounts to people, set their brand.
    manage_accounts: bool = False
    #: "Kết nối BM": tokens, tests, syncs.
    manage_connections: bool = False
    #: "Nhập doanh thu" and "KPI" (0079): a Trưởng phòng ADS, the OWNER / an ADMIN.
    edit_revenue: bool = False
    #: Works in the department (seated in ADS, or the OWNER / an ADMIN): may
    #: add their own typed-in accounts. False for a dashboard-only viewer.
    in_ads: bool = False

    def require_view(self) -> None:
        if not self.view:
            raise AdsNotVisibleError()

    def sees(self, user_id: uuid.UUID | None) -> bool:
        return self.view_all or (user_id is not None and user_id == self.user_id)


def ads_access(membership: UnitMembership) -> AdsAccess:
    if membership.sees_all:
        return AdsAccess(
            user_id=membership.user_id,
            view=True,
            view_all=True,
            manage_accounts=True,
            manage_connections=membership.is_owner,
            edit_revenue=True,
            in_ads=True,
        )
    ticked = isinstance(membership.access, Access) and membership.access.allows(
        MODULE_ADS, AdsDepartmentPermission.DASHBOARD.value
    )
    seats = [seat for seat in membership.seats if seat.code == ADS_DEPARTMENT_CODE]
    if not seats:
        return AdsAccess(user_id=membership.user_id, view=ticked, view_all=ticked)
    heads = any(seat.position is Position.HEAD for seat in seats)
    return AdsAccess(
        user_id=membership.user_id,
        view=True,
        view_all=heads or ticked,
        manage_accounts=heads,
        edit_revenue=heads,
        in_ads=True,
    )


__all__ = ["AdsAccess", "AdsNotVisibleError", "ads_access"]
