"""The fallback member code is the first word of the name, folded to ASCII."""

from __future__ import annotations

import pytest

from meobot.domain.units.member_code import derive_member_code


@pytest.mark.parametrize(
    ("name", "code"),
    [
        ("Tuấn Anh Lê", "TUAN"),
        ("Đỗ Văn Hùng", "DO"),
        ("Hiếu Hoàng", "HIEU"),
        ("Hà", "HA"),
        ("A", "ATV"),
        ("  ", "TV"),
        ("Nguyễn-Phương", "NGUYEN"),
        ("Trưởng phòng MKT", "TRUONG"),
        ("Alexanderson", "ALEXANDE"),
    ],
)
def test_the_code_is_the_first_word_in_ascii_capitals(name: str, code: str) -> None:
    assert derive_member_code(name) == code
