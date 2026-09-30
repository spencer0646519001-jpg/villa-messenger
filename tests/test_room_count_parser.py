import pytest

from app.domain.room_count_parser import (
    count_room_mentions,
    parse_plain_room_count_answer,
    parse_room_count,
    parse_room_count_answer,
)


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


@pytest.mark.parametrize(
    ("text", "expected"),
    [("4", 4), ("開3房", 3), ("四間房", 4), ("4間", 4), ("開 2 間", 2), ("3房就好", 3)],
)
def test_parse_plain_room_count_answer_accepts_single_room_answers(text: str, expected: int) -> None:
    assert parse_plain_room_count_answer(text) == expected


@pytest.mark.parametrize("text", ["全部", "包棟", "4人", "4人2間 2人2間", "那就開3房好了謝謝"])
def test_parse_plain_room_count_answer_rejects_anything_else(text: str) -> None:
    assert parse_plain_room_count_answer(text) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [("4人2間 2人2間", 2), ("4人房2間 2人房2間", 2), ("開3房", 1), ("全部", 0), ("8大4小", 0)],
)
def test_count_room_mentions(text: str, expected: int) -> None:
    assert count_room_mentions(text) == expected
