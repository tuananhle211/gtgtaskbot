"""The design codes ("mã task design") an order carries, with no I/O.

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
from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

#: The quantity of one code on one order.
MAX_QUANTITY = 999
#: How many different codes one order may carry.
MAX_ITEMS = 30


@dataclass(frozen=True, slots=True)
class DesignItem:
    """One code on one order, as stored: a snapshot of the catalogue row.

    Since 0074 it is a "mã task công việc" of any phase (Biên kịch N…, Design
    T…, Dựng D…) and carries what its points are read from: the catalogue's
    points per unit, the chosen form ("hình thức", e.g. Resize 20%) and the
    line's points. A "Việc khác" line (``free``) carries the points the person
    typed in ``unit_points`` and the work it stands for in ``note``. Lists
    written before 0074 read as "Làm mới" with no points.
    """

    code_id: uuid.UUID
    code: str
    name: str
    quantity: int
    form: str = "NEW"
    form_label: str = "Làm mới"
    form_pct: int = 100
    unit_points: int | None = None
    note: str | None = None
    free: bool = False
    examples: str | None = None
    unit_label: str | None = None

    @property
    def points(self) -> int:
        """The line's points: points per unit x form % x quantity, rounded."""
        if self.unit_points is None:
            return 0
        return line_points(self.unit_points, self.form_pct, self.quantity)

    @property
    def key(self) -> tuple[uuid.UUID, str, int | None]:
        """What two lists are compared on: the code and its form - and, for a
        "Việc khác" line, the points typed in. A note is never a difference."""
        return (self.code_id, self.form, self.unit_points if self.free else None)

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "code_id": str(self.code_id),
            "code": self.code,
            "name": self.name,
            "quantity": self.quantity,
            "form": self.form,
            "form_label": self.form_label,
            "form_pct": self.form_pct,
            "unit_points": self.unit_points,
            "points": self.points,
            "note": self.note,
            "free": self.free,
        }
        if self.examples is not None:
            data["examples"] = self.examples
        if self.unit_label is not None:
            data["unit_label"] = self.unit_label
        return data


@dataclass(frozen=True, slots=True)
class DesignPick:
    """What a person sends: a catalogue row, how many, in which form - and, for
    "Việc khác", the work done (``note``) and its points."""

    code_id: uuid.UUID
    quantity: int = 1
    form: str | None = None
    note: str | None = None
    points: int | None = None


def line_points(unit_points: int, form_pct: int, quantity: int) -> int:
    """``unit x pct% x quantity``, half rounded up (90 x 20% x 6 = 108)."""
    exact = Decimal(unit_points) * Decimal(form_pct) * Decimal(quantity) / Decimal(100)
    return int(exact.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(Decimal(str(value)))


def items_from_json(raw: Any) -> tuple[DesignItem, ...]:
    """The stored list, tolerant of anything that is not one (``()``)."""
    if not isinstance(raw, list):
        return ()
    items: list[DesignItem] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        try:
            note = entry.get("note")
            examples = entry.get("examples")
            unit_label = entry.get("unit_label")
            items.append(
                DesignItem(
                    code_id=uuid.UUID(str(entry["code_id"])),
                    code=str(entry["code"]),
                    name=str(entry.get("name") or ""),
                    quantity=int(entry["quantity"]),
                    form=str(entry.get("form") or "NEW"),
                    form_label=str(entry.get("form_label") or "Làm mới"),
                    form_pct=int(entry.get("form_pct") or 100),
                    unit_points=_optional_int(entry.get("unit_points")),
                    note=None if note is None else str(note),
                    free=bool(entry.get("free", False)),
                    examples=None if examples is None else str(examples),
                    unit_label=None if unit_label is None else str(unit_label),
                )
            )
        except (KeyError, TypeError, ValueError, ArithmeticError):
            continue
    return tuple(items)


def items_to_json(items: Iterable[DesignItem]) -> list[dict[str, Any]]:
    return [item.to_json() for item in items]


def same_items(left: Iterable[DesignItem], right: Iterable[DesignItem]) -> bool:
    """Whether two lists ask for the same work: the same codes in the same
    forms with the same quantities (and, for "Việc khác", the same points), in
    any order. A renamed catalogue row or an edited note is not a difference."""
    return _quantities(left) == _quantities(right)


def _quantities(items: Iterable[DesignItem]) -> dict[tuple[uuid.UUID, str, int | None], int]:
    totals: dict[tuple[uuid.UUID, str, int | None], int] = {}
    for item in items:
        totals[item.key] = totals.get(item.key, 0) + item.quantity
    return totals


def total_points(items: Iterable[DesignItem]) -> int:
    return sum(item.points for item in items)


def describe_item(item: DesignItem) -> str:
    """``T1 x6 · Resize`` / ``Việc khác (60đ): tách nền`` - one line."""
    if item.free:
        text = f"Việc khác ({item.points}đ)"
        return f"{text}: {item.note}" if item.note else text
    text = f"{item.code} x{item.quantity}"
    return text if item.form == "NEW" else f"{text} · {item.form_label}"


def describe_items(items: Iterable[DesignItem]) -> str:
    """``TKBTS x3, T1 x6 · Resize`` - for a history line."""
    return ", ".join(describe_item(item) for item in items) or "(trống)"


def sum_work_items(lists: Iterable[Iterable[DesignItem]]) -> tuple[DesignItem, ...]:
    """0069: several designers' lists added into one, quantities summed per
    code and form (0074). The snapshot is the first one seen for each. Used to
    compare the whole Design team's output against the order's list."""
    total: dict[tuple[uuid.UUID, str, int | None], DesignItem] = {}
    for items in lists:
        for item in items:
            seen = total.get(item.key)
            total[item.key] = (
                item if seen is None else replace(seen, quantity=seen.quantity + item.quantity)
            )
    return tuple(total.values())


#: How many "Ảnh minh hoạ" links one birthday order carries.
MAX_REFERENCE_LINKS = 20


def split_links(stored: str | None) -> tuple[str, ...]:
    """The links kept in one text column, one per line (a birthday order's
    "Ảnh minh hoạ"). An order with a single link reads as one."""
    if not stored:
        return ()
    return tuple(line.strip() for line in stored.splitlines() if line.strip())


def join_links(links: Iterable[str]) -> str | None:
    """Links as stored: one per line, blanks and repeats dropped, in order."""
    kept = list(dict.fromkeys(part for link in links for part in split_links(link)))
    return "\n".join(kept) or None


__all__ = [
    "MAX_ITEMS",
    "MAX_QUANTITY",
    "MAX_REFERENCE_LINKS",
    "DesignItem",
    "DesignPick",
    "describe_item",
    "describe_items",
    "items_from_json",
    "items_to_json",
    "join_links",
    "line_points",
    "same_items",
    "split_links",
    "sum_work_items",
    "total_points",
]
