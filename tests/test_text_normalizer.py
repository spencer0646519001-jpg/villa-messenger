import pytest

from app.domain.text_normalizer import normalize_for_parsing


@pytest.mark.parametrize(
    "full_width, expected",
    [
        ("／", "/"),          # full-width slash  U+FF0F
        ("０", "0"),          # full-width digits U+FF10-19
        ("９", "9"),
        ("６／１４", "6/14"),
        ("　", " "),          # full-width space  U+3000
        ("：", ":"),          # full-width colon  U+FF1A
    ],
)
def test_full_width_punctuation_and_digits_fold_to_half_width(full_width, expected):
    assert normalize_for_parsing(full_width) == expected


@pytest.mark.parametrize("ideograph", ["入住", "退房", "沒水", "大人", "毛孩"])
def test_han_ideographs_are_unchanged(ideograph):
    assert normalize_for_parsing(ideograph) == ideograph


@pytest.mark.parametrize("text", ["6/14", "5/12 入住 5/14 退房 2大1小", "abc 123"])
def test_half_width_input_is_idempotent(text):
    assert normalize_for_parsing(text) == text
