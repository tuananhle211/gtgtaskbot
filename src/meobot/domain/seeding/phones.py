"""Reading a batch of phone numbers, with no I/O (0085).

One number per line. Spaces, dots, dashes and brackets are dropped and a
leading ``00`` becomes ``+``. A Vietnamese number is kept as digits only,
starting with 0: ``+84`` / ``84`` become ``0``, and a 9-digit mobile that lost
its leading 0 (a spreadsheet's doing) gets it back. Anything else (foreign
numbers) is kept as written - an optional ``+`` then digits - with no check of
the country's format. Only a line that is not digits, or has fewer than
``MIN_DIGITS`` / more than ``MAX_DIGITS`` digits, is invalid.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

MAX_LINES = 1000
MIN_DIGITS = 6
#: E.164 allows 15; the column holds 20 characters.
MAX_DIGITS = 15
_JUNK = re.compile(r"[\s.\-()]")

VALID = "NEW"
INVALID = "INVALID"
#: Already handed in (and not refused) by anybody.
DUPLICATE = "DUPLICATE"
#: Twice in the same batch.
REPEATED = "REPEATED"


def normalise_phone(raw: str) -> str | None:
    digits = _JUNK.sub("", raw.strip())
    if digits.startswith("00"):
        digits = "+" + digits[2:]
    if digits.startswith("+84"):
        digits = "0" + digits[3:]
    elif digits.startswith("84") and len(digits) in (11, 12):
        digits = "0" + digits[2:]
    plus = digits.startswith("+")
    body = digits[1:] if plus else digits
    if not body.isdigit() or not body.isascii():
        return None
    if not plus and len(body) == 9 and body[0] in "35789":
        return "0" + body
    if not MIN_DIGITS <= len(body) <= MAX_DIGITS:
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
    "MAX_DIGITS",
    "MAX_LINES",
    "MIN_DIGITS",
    "REPEATED",
    "VALID",
    "PhoneLine",
    "normalise_phone",
    "read_lines",
    "week_start",
]
