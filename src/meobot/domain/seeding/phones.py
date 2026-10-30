"""Reading a batch of phone numbers, with no I/O (0085).

One number per line. A number is kept as digits only, starting with 0:
spaces, dots, dashes and brackets are dropped, ``+84`` / ``84`` become ``0``,
and a 9-digit mobile that lost its leading 0 (a spreadsheet's doing) gets it
back. 10 digits (mobiles) or 11 (old landlines) - anything else is invalid.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

MAX_LINES = 1000
_JUNK = re.compile(r"[\s.\-()]")

VALID = "NEW"
INVALID = "INVALID"
#: Already handed in (and not refused) by anybody.
DUPLICATE = "DUPLICATE"
#: Twice in the same batch.
REPEATED = "REPEATED"


def normalise_phone(raw: str) -> str | None:
    digits = _JUNK.sub("", raw.strip())
    if digits.startswith("+84"):
        digits = "0" + digits[3:]
    elif digits.startswith("84") and len(digits) in (11, 12):
        digits = "0" + digits[2:]
    if not digits.isdigit():
        return None
    if len(digits) == 9 and digits[0] in "35789":
        digits = "0" + digits
    if not digits.startswith("0") or len(digits) not in (10, 11):
        return None
    return digits


@dataclass(frozen=True, slots=True)
class PhoneLine:
    number: int
    raw: str
    phone: str | None


def read_lines(text: str) -> list[PhoneLine]:
    """The non-blank lines, numbered from 1, each with its number or None."""
    lines = []
    for number, raw in enumerate(text.splitlines(), start=1):
        if raw.strip():
            lines.append(PhoneLine(number=number, raw=raw.strip(), phone=normalise_phone(raw)))
    return lines


def week_start(day: date) -> date:
    """The Monday of ``day``'s week."""
    return day - timedelta(days=day.weekday())


__all__ = [
    "DUPLICATE",
    "INVALID",
    "MAX_LINES",
    "REPEATED",
    "VALID",
    "PhoneLine",
    "normalise_phone",
    "read_lines",
    "week_start",
]
