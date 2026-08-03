import pytest

from app.domain.bbq_parser import parse_bbq


@pytest.mark.parametrize(
    "text",
    [
        "是否烤肉 (酌收清潔費 NT1,000)：是",
        "是否烤肉:要",
        "是否烤肉:需要",
        "是否烤肉 (酌收清潔費 NT1,000):\n是",
        "要烤肉",
        "需要BBQ",
    ],
)
def test_affirmative_bbq_answers(text: str) -> None:
    assert parse_bbq(text).wants_bbq is True


@pytest.mark.parametrize(
    "text",
    [
        "是否烤肉 (酌收清潔費 NT1,000)：否",
        "是否烤肉:不用",
        "是否烤肉:不需要",
        "是否烤肉 (酌收清潔費 NT1,000):\n否",
        "不用烤肉",
        "不需要BBQ",
        "沒有要烤肉",
    ],
)
def test_negative_bbq_answers(text: str) -> None:
    assert parse_bbq(text).wants_bbq is False


def test_no_bbq_mention_defaults_false() -> None:
    assert parse_bbq("入住日期:8/4-8/6").wants_bbq is False


def test_shi_fou_label_is_not_misread_as_negation_or_affirmation_by_itself() -> None:
    # "是否烤肉" alone (no answer at all) must not be misread from the "是"
    # inside "是否" -- it should fall through to the default "no" only via
    # the explicit "no mention matched" path, not because "是" was consumed
    # as a false affirmative.
    result = parse_bbq("是否烤肉 (酌收清潔費 NT1,000)")
    assert result.wants_bbq is False


def test_next_line_answer_does_not_skip_past_an_unrelated_later_field() -> None:
    # A blank bbq answer followed by a BLANK line then an unrelated field
    # that itself starts with an affirmative-looking word ("有沒有...") must
    # NOT let the colon-gap regex skip past the blank line to grab it.
    text = "是否烤肉 (酌收清潔費 NT1,000)：\n\n有沒有停車位"
    assert parse_bbq(text).wants_bbq is False
