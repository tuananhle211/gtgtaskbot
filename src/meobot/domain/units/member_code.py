"""A member code made from a name, for an orderer who was never given one.

The admin screen is where codes are assigned. This is the fallback behind it:
a head or the owner placing their first order should not be stopped by a
field nobody filled in. The result is what an admin would probably have
typed - the first word of the name in ASCII capitals - and it obeys the same
rule as a typed code (``MEMBER_CODE_PATTERN``: 2 to 12 letters or digits).
"""

from __future__ import annotations

import re
import unicodedata

_MAX_LENGTH = 8
_FALLBACK = "TV"
_NOT_ALNUM = re.compile(r"[^A-Za-z0-9]+")


def fold_ascii(text: str) -> str:
    """Strip Vietnamese diacritics: ``Tuấn`` → ``Tuan``, ``Đỗ`` → ``Do``."""
    swapped = text.replace("Đ", "D").replace("đ", "d")
    decomposed = unicodedata.normalize("NFD", swapped)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def derive_member_code(full_name: str) -> str:
    """``"Tuấn Anh Lê"`` → ``"TUAN"``; ``"Hà"`` → ``"HA"``; ``"A"`` → ``"ATV"``.

    Words are taken from the front until the code has at least two
    characters, then it is cut to eight so a long first name still reads as
    a prefix in ``TUAN-D-261007-01``.
    """
    words = [word for word in _NOT_ALNUM.split(fold_ascii(full_name)) if word]
    code = ""
    for word in words:
        code += word.upper()
        if len(code) >= 2:
            break
    if len(code) < 2:
        code += _FALLBACK
    return code[:_MAX_LENGTH]


__all__ = ["derive_member_code", "fold_ascii"]
