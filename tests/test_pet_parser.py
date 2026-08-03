import pytest

from app.domain.pet_parser import parse_pet_count_answer, parse_pets


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


@pytest.mark.parametrize(
    "text",
    [
        "是否有寵物(僅限小型寵物,每隻酌收NT500):否",
        "是否有寵物:否",
        "沒有帶寵物",
        "不帶寵物",
        "不需要寵物",
    ],
)
def test_labeled_or_natural_pet_negation_is_no_pet(text: str) -> None:
    result = parse_pets(text)

    assert result.has_pet is False
    assert result.pet_count is None
    assert result.needs_pet_count_confirmation is False


def test_pet_negation_yields_to_explicit_count() -> None:
    result = parse_pets("沒有帶大狗但有帶1隻小狗")

    assert result.has_pet is True
    assert result.pet_count == 1


@pytest.mark.parametrize(
    "text",
    [
        "是否有寵物 (僅限小型寵物,每隻酌收 NT500):四個月小狗",
        "是否有寵物 (僅限小型寵物,每隻酌收 NT500):是",
        "是否有寵物:有養狗",
    ],
)
def test_shi_fou_label_is_not_misread_as_negation(text: str) -> None:
    # Regression: "是否有寵物" contains "否" (as part of "是否" = "whether"),
    # which must NOT be treated as a negation answer regardless of what the
    # customer actually filled in after the colon.
    result = parse_pets(text)

    assert result.has_pet is True
    assert result.needs_pet_count_confirmation is True


def test_shi_fou_label_with_explicit_negation_answer_is_still_no_pet() -> None:
    result = parse_pets("是否有寵物:否")

    assert result.has_pet is False


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1隻", 1),
        ("1只", 1),
        ("1", 1),
        ("一隻", 1),
        ("2隻", 2),
        ("  3隻  ", 3),
    ],
)
def test_parse_pet_count_answer(text: str, expected: int) -> None:
    assert parse_pet_count_answer(text) == expected


@pytest.mark.parametrize("text", ["", "沒有", "1隻狗", "幾隻都可以"])
def test_parse_pet_count_answer_rejects_non_bare_answers(text: str) -> None:
    # Repeating the pet noun, or anything not a bare number(+unit), is not a
    # contextual count answer -- parse_pets() already handles those cases.
    assert parse_pet_count_answer(text) is None
