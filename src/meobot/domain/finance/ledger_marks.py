"""The hand-picked columns of the accounting sheet "BCLT new" (13/11/2026).

Each BCLT row (an order on one day) carries five marks Kế toán picks from the
lists on the workbook's "GHI CHÚ" tab. APM suggests what it can read off the
CRM (``suggested`` on the row) and keeps what Kế toán picks
(``finance_fees``, 0108), which always wins.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class MarkSpec:
    key: str
    label: str
    choices: tuple[str, ...]


#: In the sheet's column order (B..F of "BCLT new").
MARKS: tuple[MarkSpec, ...] = (
    MarkSpec("status2", "Tình trạng 2", ("Đã lên đơn PM", "Đã đăng ảnh", "Đã làm cả 2")),
    MarkSpec("customer", "Mới", ("Mới", "Cũ")),
    MarkSpec(
        "status",
        "Tình trạng",
        ("đã Cọc TT", "đã Cọc TN", "Cọc CS TN", "Cọc onl TN", "TTCNTT", "TTCNTN"),
    ),
    MarkSpec("upsale", "Up", ("Up", "Sale", "GT")),
    MarkSpec("category", "Phân loại", ("V1", "V3", "Da", "CSCB", "Tiêm", "DV #")),
)
MARK_KEYS: tuple[str, ...] = tuple(spec.key for spec in MARKS)
MARK_SPECS: Mapping[str, MarkSpec] = MappingProxyType({spec.key: spec for spec in MARKS})


def mark_value(key: str, value: object) -> str | None:
    """The stored form of a picked mark: one of the list (case and spaces
    forgiven), blank = cleared; anything else -> ``ValueError``."""
    if value is None or str(value).strip() == "":
        return None
    wanted = " ".join(str(value).split()).casefold()
    for choice in MARK_SPECS[key].choices:
        if choice.casefold() == wanted:
            return choice
    raise ValueError(value)


def category_of(service_names: list[str]) -> str | None:
    """ "PHÂN LOẠI" read off the services: the V1 / V3 packages end their code
    with it (HAMLV3, MBV3, TV3…). Nothing else is guessed."""
    tails = {name.strip().upper()[-2:] for name in service_names if name}
    for choice in ("V3", "V1"):
        if choice in tails:
            return choice
    return None


__all__ = ["MARKS", "MARK_KEYS", "MARK_SPECS", "MarkSpec", "category_of", "mark_value"]
