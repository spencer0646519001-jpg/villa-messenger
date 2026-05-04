import pytest

from app.domain.number_parser import parse_chinese_or_arabic_number


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1", 1),
        ("2", 2),
        ("10", 10),
        ("12", 12),
        ("16", 16),
        ("一", 1),
        ("二", 2),
        ("兩", 2),
        ("三", 3),
        ("四", 4),
        ("五", 5),
        ("六", 6),
        ("七", 7),
        ("八", 8),
        ("九", 9),
        ("十", 10),
        ("十一", 11),
        ("十二", 12),
        ("十三", 13),
        ("十四", 14),
        ("十五", 15),
        ("十六", 16),
    ],
)
def test_parse_supported_numbers(text: str, expected: int) -> None:
    assert parse_chinese_or_arabic_number(text) == expected


def test_unknown_number_returns_none() -> None:
    assert parse_chinese_or_arabic_number("二十") is None
