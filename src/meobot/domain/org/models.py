"""Departments ("Phòng ban"), the seats people hold in them, and grants.

Three layers decide what a person may do (``docs/org/ORG_AND_PERMISSIONS_PLAN.md``):

1. the **system role** - the OWNER and an ADMIN are above everything;
2. **departments** - data, not code. A person may sit in several, in each as
   head (``HEAD``) or member (``MEMBER``), and report to one head there (their
   manager: "nhân viên của ai người đó quản lý"). A department may have
   several heads - one per branch;
3. **modules** (the order flow today; resources, HR... later) declare their
   permissions, and each department grants them per position with a scope.
   What a person may do is the union over their seats.

A department's **function** says what it is to the rest of the app: one of
the three production steps of the order flow, the PR team, the board of
directors, the resources department - or nothing special (ADS, HR, Finance,
Legal...: what they may do comes from their grants alone).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from meobot.domain.orders.models import OrderNodeType


class DepartmentFunction(StrEnum):
    NONE = "NONE"
    #: The order flow's production steps (Biên kịch, Design, Dựng).
    BIEN_TAP = "BIEN_TAP"
    THIET_KE = "THIET_KE"
    DUNG = "DUNG"
    #: The PR team: its people are the PR stream.
    PR = "PR"
    #: Ban Giám đốc: sees every report and dashboard, decides nothing by it.
    BOARD = "BOARD"
    #: Phòng Tài nguyên (the resources module, later).
    RESOURCES = "RESOURCES"


class Position(StrEnum):
    HEAD = "HEAD"
    MEMBER = "MEMBER"


#: The modules whose permissions departments grant: the order flow, and
#: (0065) the resources module - see :mod:`meobot.domain.org.modules`.
MODULE_ORDER = "ORDER"

FUNCTION_LABELS: MappingProxyType[DepartmentFunction, str] = MappingProxyType(
    {
        DepartmentFunction.NONE: "Phòng thường",
        DepartmentFunction.BIEN_TAP: "Sản xuất: Biên kịch",
        DepartmentFunction.THIET_KE: "Sản xuất: Design",
        DepartmentFunction.DUNG: "Sản xuất: Dựng",
        DepartmentFunction.PR: "PR Thương Hiệu",
        DepartmentFunction.BOARD: "Ban Giám đốc",
        DepartmentFunction.RESOURCES: "Tài nguyên",
    }
)
POSITION_LABELS: MappingProxyType[Position, str] = MappingProxyType(
    {Position.HEAD: "Trưởng phòng", Position.MEMBER: "Nhân viên"}
)

#: The production step each production function runs.
FUNCTION_NODES: MappingProxyType[DepartmentFunction, OrderNodeType] = MappingProxyType(
    {
        DepartmentFunction.BIEN_TAP: OrderNodeType.BIEN_TAP,
        DepartmentFunction.THIET_KE: OrderNodeType.THIET_KE,
        DepartmentFunction.DUNG: OrderNodeType.DUNG,
    }
)
PRODUCTION_FUNCTIONS: frozenset[DepartmentFunction] = frozenset(FUNCTION_NODES)


@dataclass(frozen=True, slots=True)
class Seat:
    """One person's open place in one department."""

    department_id: uuid.UUID
    code: str
    name: str
    function: DepartmentFunction
    position: Position
    #: The head of this department the person reports to, if any.
    manager_user_id: uuid.UUID | None = None
    sort_order: int = 0

    @property
    def node(self) -> OrderNodeType | None:
        """The production step this seat works on, if it is a production one."""
        return FUNCTION_NODES.get(self.function)


@dataclass(frozen=True, slots=True)
class Grant:
    """One permission of one module, with its scope, as a seat holds it.
    ``function`` is the granting department's: an ``OWN`` node permission
    reaches that department's production step."""

    permission: str
    scope: str
    function: DepartmentFunction = DepartmentFunction.NONE


#: The departments a fresh database starts with: (code, name, function, order).
DEFAULT_DEPARTMENTS: tuple[tuple[str, str, DepartmentFunction, int], ...] = (
    ("ADS", "ADS", DepartmentFunction.NONE, 0),
    ("BT", "Biên Tập", DepartmentFunction.BIEN_TAP, 1),
    ("TK", "Thiết Kế", DepartmentFunction.THIET_KE, 2),
    ("D", "Dựng", DepartmentFunction.DUNG, 3),
    ("PR", "PR Thương Hiệu", DepartmentFunction.PR, 4),
    ("TN", "Tài nguyên", DepartmentFunction.RESOURCES, 5),
    ("BGD", "Ban Giám đốc", DepartmentFunction.BOARD, 6),
)

#: What each starting department grants in the order flow, per position. It
#: reproduces the permission matrix the order flow had before departments:
#: ADS = the old head / orderer columns (a head's view and approval reach their
#: own team, ``OWN``); the production departments = the Leader / staff columns.
DEFAULT_ORDER_GRANTS: MappingProxyType[
    str, MappingProxyType[Position, MappingProxyType[str, str]]
] = MappingProxyType(
    {
        "ADS": MappingProxyType(
            {
                Position.HEAD: MappingProxyType(
                    {
                        "VIEW": "OWN",
                        "ORDER_CREATE": "ALL",
                        "ORDER_APPROVE": "OWN",
                        "FINAL_REVIEW": "ALL",
                        "PRIORITY": "ALL",
                        "CANCEL": "ALL",
                    }
                ),
                Position.MEMBER: MappingProxyType(
                    {"VIEW": "OWN", "ORDER_CREATE": "ALL", "CANCEL": "OWN"}
                ),
            }
        ),
        **{
            code: MappingProxyType(
                {
                    Position.HEAD: MappingProxyType(
                        {
                            "VIEW": "OWN",
                            "NODE_ASSIGN": "OWN",
                            "NODE_REVIEW": "OWN",
                            "VIDEO_REVIEW": "OWN",
                            # 0062: the birthday board is Thiết Kế's.
                            **({"BIRTHDAY": "ALL"} if code == "TK" else {}),
                        }
                    ),
                    Position.MEMBER: MappingProxyType(
                        {"VIEW": "OWN", **({"BIRTHDAY": "ALL"} if code == "TK" else {})}
                    ),
                }
            )
            for code in ("BT", "TK", "D")
        },
    }
)


def normalise_department_code(value: str) -> str:
    """A department code: A-Z and 0-9, at most 12 (it prefixes other codes)."""
    return "".join(character for character in value.upper() if character.isalnum())[:12]


__all__ = [
    "DEFAULT_DEPARTMENTS",
    "DEFAULT_ORDER_GRANTS",
    "FUNCTION_LABELS",
    "FUNCTION_NODES",
    "MODULE_ORDER",
    "POSITION_LABELS",
    "PRODUCTION_FUNCTIONS",
    "DepartmentFunction",
    "Grant",
    "Position",
    "Seat",
    "normalise_department_code",
]
