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
        # Real LINE E2E regression: "想加烤肉" (a very common customer
        # phrasing) fell through to mentioned=False because "想" wasn't
        # recognized as a request word and "要" in "我要訂房" sits too far
        # from "烤肉" to match -- see test_line_webhook.py's
        # test_bbq_request_with_pet_persists_wants_bbq_on_first_turn for the
        # full end-to-end reproduction.
        "我要訂房，想加烤肉，有帶一隻狗",
        "8/20-8/22入住,8大2小,想加烤肉",
        "想加烤肉",
        "想烤肉",
        # Codex review: "不"/"沒" appearing nearby but NOT actually negating
        # the BBQ request (a discourse marker, or negating something else
        # entirely) must not be misread as a decline.
        "沒問題，想加烤肉",
        "不過想加烤肉",
        # Codex review: negating something ELSE ("加床" = an extra bed) in
        # the same message must not consume a separate, later, genuinely
        # affirmative BBQ clause just because both fall within the
        # negation term's gap to the BBQ term.
        "不想加床，要烤肉",
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
        "不要烤肉",
        "不要BBQ",
        # "不想"/"不太想" are literal negation terms (see bbq_parser.py) so
        # they share the same 4-char gap to the BBQ term as every other
        # negation term -- wide enough to still catch a decline with an
        # extra verb in between ("不想要參加烤肉"), which a tighter gap
        # tied specifically to "想" missed in an earlier version of the fix.
        "不想烤肉",
        "不想要烤肉",
        "不太想烤肉",
        "不想要參加烤肉",
        # "沒想" alongside "不想" -- same decline, different negation word.
        "沒想要烤肉",
    ],
)
def test_negative_bbq_answers(text: str) -> None:
    assert parse_bbq(text).wants_bbq is False


@pytest.mark.parametrize(
    "text",
    [
        "是否烤肉 (酌收清潔費 NT1,000)：是",
        "不用烤肉",
        "不要烤肉",
    ],
)
def test_mentioned_true_when_answer_is_explicit(text: str) -> None:
    assert parse_bbq(text).mentioned is True


@pytest.mark.parametrize(
    "text",
    [
        "入住日期:8/4-8/6",
        "是否烤肉 (酌收清潔費 NT1,000)",
        # Codex review of the first version of the "想" fix (P1): "想" only
        # counts as a BBQ request within a tight gap of the BBQ term -- a
        # question about the fee ("想問一下烤肉費用") must NOT match just
        # because "想" appears somewhere earlier in the same sentence.
        "想問一下烤肉費用",
    ],
)
def test_mentioned_false_when_no_explicit_answer(text: str) -> None:
    # A bare form question with no answer filled in (or no BBQ mention at
    # all) must not be treated as an explicit statement -- callers rely on
    # mentioned=False to avoid clobbering an existing wants_bbq flag just
    # because the BBQ term appears somewhere in the text.
    assert parse_bbq(text).mentioned is False


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
