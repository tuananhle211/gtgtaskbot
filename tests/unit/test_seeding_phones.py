"""Reading seeding phone numbers: Vietnamese numbers normalised, foreign ones kept."""

from __future__ import annotations

import pytest

from meobot.domain.seeding.phones import normalise_phone


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0912 345 678", "0912345678"),
        ("+84 987 654 321", "0987654321"),
        ("84987654321", "0987654321"),
        ("0084 987 654 321", "0987654321"),
        ("912345678", "0912345678"),
        ("02838123456", "02838123456"),
        # Foreign numbers: kept as written, no format check.
        ("+1 (415) 555-2671", "+14155552671"),
        ("+49 176 1234 5678", "+4917612345678"),
        ("0049 176 1234 5678", "+4917612345678"),
        ("+66 81 234 5678", "+66812345678"),
        ("4155552671", "4155552671"),
        ("012345", "012345"),
    ],
)
def test_accepted(raw: str, expected: str) -> None:
    assert normalise_phone(raw) == expected


@pytest.mark.parametrize("raw", ["abc", "+", "12345", "091x345678", "+1234567890123456", "++84912"])
def test_refused(raw: str) -> None:
    assert normalise_phone(raw) is None
