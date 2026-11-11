"""Chăm sóc khách hàng ("CSKH"): its permission module (rewritten 0102, 2026-11-11).

The module is the company inbox of the Facebook pages managed in Pancake plus
the telesales' Zalo nicks (see :mod:`meobot.domain.cskh.models`):

* ``INBOX`` - "Hộp chat page": ``OWN`` = the pages one is a member of (Chủ page
  / Nhân viên trực, set on "Kết nối page"), ``ALL`` = every page;
* ``ZALO`` - "Hộp chat Zalo": ``OWN`` = the nicks handed to one, ``ALL`` = every
  nick (a manager);
* ``MANAGE`` - "Kết nối page / Zalo", labels and quick replies, the inbox
  settings, Pancake and POS keys; may also change a locked marketer.

OWNER and ADMIN hold everything by role. 0102 gives the old ``REPLY`` holders
``INBOX ALL`` and seeds the Tư vấn online / Telesales departments.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType

from meobot.domain.org.models import DepartmentFunction, Position

MODULE_CSKH = "CSKH"


class CskhPermission(StrEnum):
    INBOX = "INBOX"
    ZALO = "ZALO"
    MANAGE = "MANAGE"


CSKH_PERMISSION_LABELS: Mapping[CskhPermission, str] = MappingProxyType(
    {
        CskhPermission.INBOX: "Hộp chat page: đọc và trả lời khách, xác nhận SĐT / QR",
        CskhPermission.ZALO: "Hộp chat Zalo telesale: xem và ghép hội thoại",
        CskhPermission.MANAGE: "Kết nối page / Zalo, câu mẫu, nhãn và cài đặt hộp chat",
    }
)

CSKH_OWN_MEANING: Mapping[CskhPermission, str] = MappingProxyType(
    {
        CskhPermission.INBOX: "Các page mình là chủ page hoặc nhân viên trực",
        CskhPermission.ZALO: "Các nick Zalo được giao cho mình",
    }
)

CSKH_SCOPE_LABELS: Mapping[CskhPermission, Mapping[str, str]] = MappingProxyType(
    {
        CskhPermission.INBOX: MappingProxyType(
            {"NONE": "Không", "OWN": "Page được giao", "ALL": "Mọi page"}
        ),
        CskhPermission.ZALO: MappingProxyType(
            {"NONE": "Không", "OWN": "Nick của mình", "ALL": "Mọi nick"}
        ),
    }
)

CSKH_OWNER_ONLY: frozenset[str] = frozenset()


def cskh_allowed_scopes(permission: CskhPermission) -> tuple[str, ...]:
    if permission is CskhPermission.MANAGE:
        return ("NONE", "ALL")
    return ("NONE", "OWN", "ALL")


def default_cskh_grants(_function: DepartmentFunction) -> dict[Position, dict[str, str]]:
    """New departments start with nothing here (0102 seeds TVOL / TELE by code)."""
    return {}


__all__ = [
    "CSKH_OWNER_ONLY",
    "CSKH_OWN_MEANING",
    "CSKH_PERMISSION_LABELS",
    "CSKH_SCOPE_LABELS",
    "MODULE_CSKH",
    "CskhPermission",
    "cskh_allowed_scopes",
    "default_cskh_grants",
]
