import pytest

from app.domain.pet_parser import parse_pets


@pytest.mark.parametrize(
    "text",
    [
        "毛孩",
        "毛小孩",
        "狗",
        "狗狗",
        "小狗",
        "帶毛孩",
        "帶狗",
    ],
)
def test_parse_dog_mentions_without_count(text: str) -> None:
    result = parse_pets(text)

    assert result.has_pet is True
    assert result.pet_type == "dog"
    assert result.pet_count is None
    assert result.needs_pet_count_confirmation is True


@pytest.mark.parametrize(
    ("text", "expected_count"),
    [
        ("一隻毛孩", 1),
        ("帶一隻毛孩", 1),
        ("兩隻狗", 2),
        ("2隻狗", 2),
        ("帶2隻狗", 2),
        ("1毛孩", 1),
    ],
)
def test_parse_pet_count(text: str, expected_count: int) -> None:
    result = parse_pets(text)

    assert result.has_pet is True
    assert result.pet_type == "dog"
    assert result.pet_count == expected_count
    assert result.needs_pet_count_confirmation is False


def test_generic_pet_mentions_need_count_and_type_confirmation() -> None:
    result = parse_pets("可以帶寵物嗎")

    assert result.has_pet is True
    assert result.pet_type is None
    assert result.pet_count is None
    assert result.needs_pet_count_confirmation is True


def test_no_pet_mention() -> None:
    result = parse_pets("請問四個人多少錢")

    assert result.has_pet is False
    assert result.pet_count is None
    assert result.needs_pet_count_confirmation is False
