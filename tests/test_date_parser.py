import pytest

from app.domain.date_parser import parse_stay_dates


@pytest.mark.parametrize(
    "text",
    [
        "5/12入住 5/14退房",
        "5月12日入住，5月14日退房",
        "入住5/12退房5/14",
    ],
)
def test_parse_two_night_stay_dates(text: str) -> None:
    result = parse_stay_dates(text, reference_year=2026)

    assert result.checkin_date == "2026-05-12"
    assert result.checkout_date == "2026-05-14"
    assert result.nights == 2
    assert result.confidence == "high"
    assert result.missing_fields == []


def test_parse_one_night_stay_dates() -> None:
    result = parse_stay_dates("5/12 入住，5/13 退房", reference_year=2026)

    assert result.checkin_date == "2026-05-12"
    assert result.checkout_date == "2026-05-13"
    assert result.nights == 1
    assert result.confidence == "high"


def test_parse_two_unlabeled_explicit_dates_in_order() -> None:
    result = parse_stay_dates("5/12 5/14", reference_year=2026)

    assert result.checkin_date == "2026-05-12"
    assert result.checkout_date == "2026-05-14"
    assert result.nights == 2
    assert result.confidence == "high"


@pytest.mark.parametrize(
    "text,expected_checkin,expected_checkout",
    [
        ("7/17-18", "2026-07-17", "2026-07-18"),
        ("8/21-23", "2026-08-21", "2026-08-23"),
        ("7/11-12", "2026-07-11", "2026-07-12"),
        ("7/17~18", "2026-07-17", "2026-07-18"),
        ("7/17到18", "2026-07-17", "2026-07-18"),
    ],
)
def test_bare_day_range_shorthand_shares_the_month(
    text: str, expected_checkin: str, expected_checkout: str
) -> None:
    # eval control_16/candidate_17/candidate_18/control_193/control_11
    # regression: "7/17-18" means "7/17 to 7/18", not just checkin=7/17 with
    # the "-18" silently dropped.
    result = parse_stay_dates(text, reference_year=2026)

    assert result.checkin_date == expected_checkin
    assert result.checkout_date == expected_checkout


def test_full_dates_on_both_sides_of_separator_are_not_double_counted() -> None:
    # "8/10-8/12": the second side is already a full M/D date, so the
    # range-suffix shorthand must NOT also treat "8" as a bare day.
    result = parse_stay_dates("入住日期:8/10-8/12", reference_year=2026)

    assert result.checkin_date == "2026-08-10"
    assert result.checkout_date == "2026-08-12"


@pytest.mark.parametrize(
    "text,expected_checkin,expected_checkout",
    [
        ("7/17-18退房", "2026-07-17", "2026-07-18"),
        ("入住7/17-18", "2026-07-17", "2026-07-18"),
    ],
)
def test_bare_day_range_shorthand_keeps_both_ends_when_one_side_is_labeled(
    text: str, expected_checkin: str, expected_checkout: str
) -> None:
    # Codex review of commit eec20a8 (P2): classifying the shorthand's two
    # matches independently left the unlabeled side stranded once the OTHER
    # side picked up a label -- "7/17-18退房" used to return checkout=7/18
    # with checkin dropped entirely, since the "exactly two unlabeled dates"
    # fallback only fires when BOTH sides are still unlabeled.
    result = parse_stay_dates(text, reference_year=2026)

    assert result.checkin_date == expected_checkin
    assert result.checkout_date == expected_checkout


@pytest.mark.parametrize(
    "text,expected_checkin,expected_checkout",
    [
        # Real LINE E2E regression: a trailing 「入住」 after a FULL two-date
        # range attaches (via _has_close_label_after) only to the closer,
        # later date -- misreading "8/20-8/22入住" as checkin=8/22 with
        # checkout dropped entirely, instead of checkin=8/20 / checkout=8/22.
        ("8/20-8/22入住,8大2小,想加烤肉", "2026-08-20", "2026-08-22"),
        ("8/20-8/22入住", "2026-08-20", "2026-08-22"),
        # Bare-day shorthand suffers the same mislabeling for the same reason.
        ("7/17-18入住", "2026-07-17", "2026-07-18"),
    ],
)
def test_trailing_checkin_label_after_a_full_range_scopes_the_whole_range(
    text: str, expected_checkin: str, expected_checkout: str
) -> None:
    result = parse_stay_dates(text, reference_year=2026)

    assert result.checkin_date == expected_checkin
    assert result.checkout_date == expected_checkout


def test_full_date_range_pairs_across_a_spaced_separator() -> None:
    # Codex review (P2): a Chinese-style date match ("8月20日") ends right
    # after "日" with no trailing-space consumption of its own, so a spaced
    # separator left a leading space in the pair-detection gap that an
    # earlier version of the fix (which dropped horizontal tolerance BEFORE
    # the separator while fixing tolerance AFTER it) no longer accepted.
    result = parse_stay_dates("入住8月20日 - 8月22日", reference_year=2026)

    assert result.checkin_date == "2026-08-20"
    assert result.checkout_date == "2026-08-22"


def test_bare_day_range_shorthand_pairs_across_a_line_wrap() -> None:
    # Codex review of the range-pair generalization (P2): the pair-detection
    # gap must tolerate a newline between the separator and the day digits,
    # same as _RANGE_SEPARATOR_DAY_PATTERN itself already does -- a
    # horizontal-only gap missed this pairing and silently dropped checkout.
    result = parse_stay_dates("入住7/17-\n18", reference_year=2026)

    assert result.checkin_date == "2026-07-17"
    assert result.checkout_date == "2026-07-18"


def test_hyphenated_clock_time_is_not_read_as_a_second_date() -> None:
    # Codex review of commit eec20a8 (P1): "7/17-18:00" (5pm) used to be
    # read as a 7/17-7/18 stay instead of a single date with a clock time.
    result = parse_stay_dates("7/17-18:00", reference_year=2026)

    assert result.checkin_date == "2026-07-17"
    assert result.checkout_date is None


def test_hyphenated_clock_time_does_not_break_a_real_range_before_it() -> None:
    # Codex review of commit eec20a8 (P1): the spurious "14" match from
    # "-14:00" pushed unlabeled_dates to 3 entries, which broke the "exactly
    # two unlabeled dates" pairing fallback and silently discarded BOTH real
    # stay dates (8/10, 8/12), not just the bogus one.
    result = parse_stay_dates("8/10-8/12-14:00", reference_year=2026)

    assert result.checkin_date == "2026-08-10"
    assert result.checkout_date == "2026-08-12"


def test_hyphenated_clock_hour_word_is_not_read_as_a_second_date() -> None:
    # Codex review of commit ac8f084 (P1): the colon/點 guard didn't cover
    # 時, so "入住7/17-18時" (checking in around 6pm on 7/17) was promoted
    # to a false 7/17-7/18 checkout.
    result = parse_stay_dates("入住7/17-18時", reference_year=2026)

    assert result.checkin_date == "2026-07-17"
    assert result.checkout_date is None


def test_colon_field_separator_does_not_block_a_real_range() -> None:
    # Codex review of commit ac8f084 (P2): the colon guard rejected ANY
    # colon after the day, so "7/17-18: 2人" (colon as a field separator,
    # not a clock time) lost the range entirely. Only an unspaced two-digit
    # minute ("18:00") should count as a clock time.
    result = parse_stay_dates("7/17-18: 2人", reference_year=2026)

    assert result.checkin_date == "2026-07-17"
    assert result.checkout_date == "2026-07-18"


def test_single_explicit_date_is_treated_as_checkin_only() -> None:
    result = parse_stay_dates("5/12 2大1嬰兒多少錢", reference_year=2026)

    assert result.checkin_date == "2026-05-12"
    assert result.checkout_date is None
    assert result.nights is None
    assert result.confidence == "low"
    assert result.missing_fields == ["checkout_date"]


def test_single_checkout_labeled_date_is_checkout_only() -> None:
    result = parse_stay_dates("5/13 退房", reference_year=2026)

    assert result.checkin_date is None
    assert result.checkout_date == "2026-05-13"
    assert result.nights is None
    assert result.confidence == "low"
    assert result.missing_fields == ["checkin_date"]


@pytest.mark.parametrize("text", ["下週六入住", "暑假四人", "端午連假有房嗎"])
def test_vague_dates_are_not_parsed(text: str) -> None:
    result = parse_stay_dates(text, reference_year=2026)

    assert result.checkin_date is None
    assert result.checkout_date is None
    assert result.nights is None
    assert result.confidence == "low"
    assert result.missing_fields == ["checkin_date", "checkout_date"]


def test_checkout_before_checkin_is_low_confidence_without_nights() -> None:
    result = parse_stay_dates("5/14入住 5/12退房", reference_year=2026)

    assert result.checkin_date == "2026-05-14"
    assert result.checkout_date == "2026-05-12"
    assert result.nights is None
    assert result.confidence == "low"


def test_next_field_label_on_new_line_does_not_steal_the_date() -> None:
    # Real production bug: a LINE OA intake-form reply puts the date range on
    # one line and the NEXT field's label on the next line. "入住人數" starts
    # with "入住" too, and used to get misread as this date's own "checkin"
    # label via the 6-char lookahead, which doesn't stop at newlines.
    text = "入住日期:8/10-8/12\n入住人數:8人"
    result = parse_stay_dates(text, reference_year=2026)

    assert result.checkin_date == "2026-08-10"
    assert result.checkout_date == "2026-08-12"
    assert result.nights == 2
    assert result.confidence == "high"
    assert result.missing_fields == []


def test_preceding_field_label_on_previous_line_does_not_attach() -> None:
    text = "聯絡電話:0912345678\n入住日期:8/10-8/12"
    result = parse_stay_dates(text, reference_year=2026)

    assert result.checkin_date == "2026-08-10"
    assert result.checkout_date == "2026-08-12"
