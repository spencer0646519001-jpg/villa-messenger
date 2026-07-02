import pytest

from app.domain.room_count_parser import parse_room_count, parse_room_count_answer


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


def test_parse_room_count_does_not_parse_bare_number_globally() -> None:
    assert parse_room_count("4") is None
    assert parse_room_count("四") is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("4", 4),
        ("四", 4),
        ("開4", 4),
        ("开4", 4),
        ("開 四", 4),
    ],
)
def test_parse_room_count_answer_supported_patterns(text: str, expected: int) -> None:
    assert parse_room_count_answer(text) == expected


@pytest.mark.parametrize("text", ["4人", "4 人", "4大人", "住4", "4房"])
def test_parse_room_count_answer_rejects_non_answer_shapes(text: str) -> None:
    assert parse_room_count_answer(text) is None
