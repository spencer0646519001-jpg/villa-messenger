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
        # Codex review: a "想到" clause ("didn't expect you can't BBQ") must
        # not suppress a genuinely separate, later affirmative statement in
        # the SAME message -- the redaction is bounded to that one clause.
        "沒想到你們不能烤肉，我還是想要烤肉",
        # Codex review: customers routinely drop punctuation entirely -- the
        # "想到" redaction must stay bounded to a short window and NOT eat a
        # genuinely separate, later request just because nothing marks
        # where the "didn't expect" remark ends.
        "沒想到可以烤肉但我想烤肉",
        # Codex review: "想到時候" ("when the time comes") is a distinct,
        # common construction -- "想"+"到時候", NOT the "想到"
        # (think-of/realize) compound -- so it must not be redacted away.
        "我們想到時候要烤肉",
    ],
)
def test_affirmative_bbq_answers(text: str) -> None:
    assert parse_bbq(text).wants_bbq is True


def test_want_negation_terms_do_not_cross_a_list_separator() -> None:
    # Codex review: "、" enumerates coordinated items under ONE verb ("不要
    # [加床、烤肉]" = don't want [bed, BBQ]), unlike "，" which usually starts
    # a new clause -- excluding it from the negation gap (as if it were a
    # clause boundary) broke declining multiple listed items at once.
    result = parse_bbq("不要加床、烤肉")
    assert result.wants_bbq is False
    assert result.mentioned is True


def test_negation_gap_stops_at_a_new_predicate_after_list_separator() -> None:
    # Codex review: "、" can ALSO introduce a genuinely separate predicate
    # with its own verb ("不要加床、要烤肉" = don't want a bed, [but do]
    # want BBQ) -- allowing negation to cross "、" unconditionally (the fix
    # for the test above) let it reach past a fresh "要" and wrongly claim
    # the BBQ term for the wrong clause.
    result = parse_bbq("不要加床、要烤肉")
    assert result.wants_bbq is True
    assert result.mentioned is True


def test_negation_gap_crosses_a_repeated_you_predicate_after_list_separator() -> None:
    # Codex review: unlike "要" (a strong "switching to affirmative" signal
    # paired against "不要"), "有" repeated after "、" is a common PARALLEL
    # enumeration still under the SAME negation ("不要[有煙]、[有烤肉味]" =
    # no smoke, no BBQ smell -- BOTH declined), not a new predicate.
    result = parse_bbq("不要有煙、有烤肉味")
    assert result.wants_bbq is False
    assert result.mentioned is True


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


def test_affirm_gap_also_stops_at_a_clause_boundary() -> None:
    # Codex review: once the negation gap stopped at "，", "不需要加床，烤肉
    # 也不用" (BOTH the bed AND the BBQ are declined) started matching the
    # AFFIRM pattern instead -- the bare "要" trapped inside "不需要" could
    # still cross the same comma via the (then-still-unrestricted) affirm
    # gap. The postfix negation "烤肉也不用" itself isn't recognized (this
    # file only parses prefix-style negation), so the safe outcome is
    # mentioned=False, not a false wants_bbq=True.
    result = parse_bbq("不需要加床，烤肉也不用")
    assert result.wants_bbq is False
    assert result.mentioned is False


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
        # Codex review: "沒想到" ("didn't expect", never "don't want") must
        # not be misread as the "沒想" negation term just because "沒想" is
        # a literal substring of it.
        "沒想到原來烤肉要收費",
        # Codex review (P2 of the same finding): guarding the NEGATION side
        # against "想到" isn't enough on its own -- an unrelated trigger word
        # elsewhere in the SAME "想到" clause ("要" in "要收烤肉費") must not
        # fall through to the AFFIRM pattern either. The whole clause is
        # scoped out, not just the negation check.
        "沒想到要收烤肉費",
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
