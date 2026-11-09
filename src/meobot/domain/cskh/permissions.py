"""Chăm sóc khách hàng ("CSKH", 0094, 2026-11-09): its own permission module.

The module answers customers who write to the company's pages (Pancake first;
other channels later) with a Hermes agent's help - see
:mod:`meobot.domain.pancake.models`.

* ``REPLY`` (NONE / ALL): open "Chăm sóc khách hàng", read the agent's
  suggested answers, edit and send them, or dismiss them - ticked per person or
  per department (Kinh doanh - CSKH) by the OWNER;
* ``MANAGE`` (``owner_only``): wire pages, their tokens, the agent and the mode
  (gợi ý / tự động).

The OWNER holds both by role, an ADMIN ``REPLY`` only.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType

MODULE_CSKH = "CSKH"


class CskhPermission(StrEnum):
    #: Read the suggested answers, send (as is or edited) or dismiss them.
    REPLY = "REPLY"
    #: The channels: pages, tokens, agent, mode.
    MANAGE = "MANAGE"


CSKH_PERMISSION_LABELS: Mapping[CskhPermission, str] = MappingProxyType(
    {
        CskhPermission.REPLY: "Duyệt và gửi câu trả lời cho khách",
        CskhPermission.MANAGE: "Cấu hình kênh (Pancake) và agent trả lời",
    }
)

CSKH_OWNER_ONLY: frozenset[str] = frozenset({CskhPermission.MANAGE.value})


def cskh_allowed_scopes(_permission: CskhPermission) -> tuple[str, ...]:
    return ("NONE", "ALL")


__all__ = [
    "CSKH_OWNER_ONLY",
    "CSKH_PERMISSION_LABELS",
    "MODULE_CSKH",
    "CskhPermission",
    "cskh_allowed_scopes",
]
