"""The design codes ("mã video") an order carries, with no I/O.

A design code is one kind of artwork the Design team produces - ``TKBTS``
"SOCIAL - Thiết kế Banner, Thumb Video, Avatar" - from a catalogue the Design
Leader keeps (``unit_design_codes``). An order lists the codes it needs, each
with a quantity. The list is stored on the order as plain snapshots (id, code,
name, quantity), so renaming or retiring a catalogue row never rewrites an
order.

Who writes the list:

* the **script step** (Biên kịch) picks it when handing in to Design;
* the **designer** may hand in with a different list. A different list is a
  request: the hand-in waits for the Design Leader whatever the unit's review
  setting says, and only the Leader's approval makes it the order's list. On
  an order with no script step there is no list to start from, so the
  designer's first one is always such a request.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

#: The quantity of one code on one order.
MAX_QUANTITY = 999
#: How many different codes one order may carry.
MAX_ITEMS = 30


@dataclass(frozen=True, slots=True)
class DesignItem:
    """One code on one order, as stored: a snapshot of the catalogue row."""

    code_id: uuid.UUID
    code: str
    name: str
    quantity: int

    def to_json(self) -> dict[str, Any]:
        return {
            "code_id": str(self.code_id),
            "code": self.code,
            "name": self.name,
            "quantity": self.quantity,
        }


@dataclass(frozen=True, slots=True)
class DesignPick:
    """What a person sends: a catalogue row and how many."""

    code_id: uuid.UUID
    quantity: int


def items_from_json(raw: Any) -> tuple[DesignItem, ...]:
    """The stored list, tolerant of anything that is not one (``()``)."""
    if not isinstance(raw, list):
        return ()
    items: list[DesignItem] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        try:
            items.append(
                DesignItem(
                    code_id=uuid.UUID(str(entry["code_id"])),
                    code=str(entry["code"]),
                    name=str(entry["name"]),
                    quantity=int(entry["quantity"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return tuple(items)


def items_to_json(items: Iterable[DesignItem]) -> list[dict[str, Any]]:
    return [item.to_json() for item in items]


def same_items(left: Iterable[DesignItem], right: Iterable[DesignItem]) -> bool:
    """Whether two lists ask for the same work: the same codes with the same
    quantities, in any order. A renamed catalogue row is not a difference."""
    return {item.code_id: item.quantity for item in left} == {
        item.code_id: item.quantity for item in right
    }


def describe_items(items: Iterable[DesignItem]) -> str:
    """``TKBTS x3, CA x1`` - for a history line."""
    return ", ".join(f"{item.code} x{item.quantity}" for item in items) or "(trống)"


__all__ = [
    "MAX_ITEMS",
    "MAX_QUANTITY",
    "DesignItem",
    "DesignPick",
    "describe_items",
    "items_from_json",
    "items_to_json",
    "same_items",
]
