import pytest

from app.domain.room_count_parser import parse_room_count


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("3房", 3),
        ("3 房", 3),
        ("三間", 3),
        ("四間房", 4),
        ("開4", 4),
        ("開四房", 4),
        ("十間房", 10),
    ],
)
def test_parse_room_count_supported_patterns(text: str, expected: int) -> None:
    assert parse_room_count(text) == expected


def test_parse_room_count_returns_none_when_absent() -> None:
    assert parse_room_count("13人 7/28-29多少錢") is None
