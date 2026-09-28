from datetime import date

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


@pytest.mark.parametrize(
    "text",
    [
        # The real production message (2026-09-22): the customer is answering
        # "how many rooms do you want", listing room types -- a 4-person room,
        # a 4-person room and a 2-person room -- not asking about 4 April.
        "開 4/4/2，3間是多少錢?",
        "1. 開4/4/2，3間是多少錢? 2. 4間全開是多少?",
        "開4/4/2三間",
    ],
)
def test_room_configuration_is_not_read_as_a_date(text: str) -> None:
    result = parse_stay_dates(text, reference_year=2026)

    assert result.checkin_date is None
    assert result.checkout_date is None
    assert result.nights is None
    assert result.confidence == "low"
    assert result.missing_fields == ["checkin_date", "checkout_date"]


def test_room_configuration_does_not_reach_the_availability_probe() -> None:
    # Why this bug mattered: a lone checkin with no checkout makes
    # availability_probe assume a one-night stay and query the calendar, so
    # "開 4/4/2" produced a real customer-visible reply about 4/4-4/5.
    from app.domain.availability_probe import with_single_night_availability_probe
    from app.domain.inquiry_parser import parse_inquiry

    inquiry = parse_inquiry("開 4/4/2，3間是多少錢?", reference_year=2026)
    probed = with_single_night_availability_probe(inquiry, "開 4/4/2，3間是多少錢?")

    assert probed.dates.checkin_date is None
    assert probed.availability_probe_checkout is None


def test_nearby_room_count_does_not_suppress_a_genuine_date() -> None:
    result = parse_stay_dates("開3間，8/20-8/22入住", reference_year=2026)

    assert result.checkin_date == "2026-08-20"
    assert result.checkout_date == "2026-08-22"
    assert result.nights == 2


@pytest.mark.parametrize(
    ("text", "checkin", "checkout"),
    [
        ("民宿有開 4/4 嗎?", "2026-04-04", None),
        ("請問有開 4/4-6 嗎?", "2026-04-04", "2026-04-06"),
        ("有開4/4嗎", "2026-04-04", None),
        # Codex review of commit bd2c0f0 (P1): a semantic "開" rule dropped
        # the range's front half here and left 4/6 stranded as a lone
        # checkin, so availability would have been checked for 4/6-4/7 --
        # dates the customer never asked about.
        ("民宿開 4/4-4/6 的房間嗎?", "2026-04-04", "2026-04-06"),
    ],
)
def test_open_for_business_question_keeps_its_dates(
    text: str, checkin: str, checkout: str | None
) -> None:
    # "開" here means "open for business" and the date is the whole question.
    result = parse_stay_dates(text, reference_year=2026)

    assert result.checkin_date == checkin
    assert result.checkout_date == checkout


# ============================================================
# YEAR INFERENCE (reference_date)
# ============================================================

_REF = date(2026, 9, 23)


@pytest.mark.parametrize(
    ("text", "checkin", "checkout"),
    [
        # The real production enquiry. Before reference_date existed this came
        # out as THIS February -- seven months in the past -- and only reached
        # 2027 because the customer also wrote "2台車", which tripped the
        # FAQ-collision LLM trigger and the LLM fixed the year in passing.
        ("明年 2/6-8", "2027-02-06", "2027-02-08"),
        ("2/6-2/8", "2027-02-06", "2027-02-08"),
        ("今年 2/6-2/8", "2026-02-06", "2026-02-08"),
        ("後年 2/6-2/8", "2028-02-06", "2028-02-08"),
        ("隔年 2/6-2/8", "2027-02-06", "2027-02-08"),
        # Still ahead this year, so untouched.
        ("10/5-10/7", "2026-10-05", "2026-10-07"),
        ("7/17-18", "2027-07-17", "2027-07-18"),
    ],
)
def test_bare_dates_resolve_to_the_next_time_they_occur(
    text: str, checkin: str, checkout: str
) -> None:
    result = parse_stay_dates(text, reference_date=_REF)

    assert (result.checkin_date, result.checkout_date) == (checkin, checkout)


@pytest.mark.parametrize(
    ("text", "reference", "checkin", "checkout"),
    [
        ("12/31入住 1/2退房", _REF, "2026-12-31", "2027-01-02"),
        ("12/31入住 1/2退房", date(2026, 12, 20), "2026-12-31", "2027-01-02"),
        # Explicit year word, so roll-forward is off and only the new-year
        # crossing itself has to be repaired.
        ("明年 12/31入住 1/2退房", _REF, "2027-12-31", "2028-01-02"),
    ],
)
def test_new_year_stay_is_no_longer_rejected_as_out_of_order(
    text: str, reference: date, checkin: str, checkout: str
) -> None:
    # These used to parse as two same-year dates, fail pricing's
    # checkout > checkin check, and send the customer a "your dates look out
    # of order" question -- every new-year booking was turned away that way.
    result = parse_stay_dates(text, reference_date=reference)

    assert (result.checkin_date, result.checkout_date) == (checkin, checkout)
    assert result.nights == 2


@pytest.mark.parametrize(
    ("text", "reference", "checkin", "checkout"),
    [
        ("5/12 入住 5/14 退房", date(2026, 5, 13), "2026-05-12", "2026-05-14"),
        ("9/20-9/25", _REF, "2026-09-20", "2026-09-25"),
    ],
)
def test_range_straddling_today_stays_in_this_year(
    text: str, reference: date, checkin: str, checkout: str
) -> None:
    # Rolling each end on its own would put the arrival next year and leave
    # the departure in this one -- a 364-night stay. The range is one unit.
    result = parse_stay_dates(text, reference_date=reference)

    assert (result.checkin_date, result.checkout_date) == (checkin, checkout)


@pytest.mark.parametrize("reference", [_REF, date(2026, 5, 13)])
def test_reversed_dates_stay_invalid_instead_of_becoming_a_year_long_stay(
    reference: date,
) -> None:
    # A plain typo must keep reaching the existing invalid-date reply. No year
    # shuffle may turn it into a bookable (and very expensive) 363-night stay.
    result = parse_stay_dates("5/14 入住 5/12 退房", reference_date=reference)

    assert result.nights is None
    assert result.confidence == "low"


@pytest.mark.parametrize(
    ("text", "checkin", "checkout"),
    [
        ("2/6-2/8", "2026-02-06", "2026-02-08"),
        ("12/31入住 1/2退房", "2026-12-31", "2026-01-02"),
        ("明年 2/6-8", "2026-02-06", "2026-02-08"),
    ],
)
def test_reference_year_alone_keeps_the_old_behaviour(
    text: str, checkin: str, checkout: str
) -> None:
    # Every pre-existing caller and test passes reference_year only; none of
    # them may start seeing rolled dates.
    result = parse_stay_dates(text, reference_year=2026)

    assert (result.checkin_date, result.checkout_date) == (checkin, checkout)


@pytest.mark.parametrize(
    ("text", "checkin", "checkout"),
    [
        # Two year words, two different years -- an ordinary way to write a
        # new-year stay. Codex review of commits 8c9aeb3 and fea1e5e (P1
        # twice): a message-wide year word shifted the whole booking, and the
        # "give up and roll forward instead" patch merely moved which of these
        # two came out a year early. Each date now takes the nearest year word
        # before it.
        ("今年12/31入住，明年1/2退房", "2026-12-31", "2027-01-02"),
        ("明年12/31入住，後年1/2退房", "2027-12-31", "2028-01-02"),
        # One word before both dates still scopes both.
        ("明年 12/31入住 1/2退房", "2027-12-31", "2028-01-02"),
        ("明年或隔年 2/6-8", "2027-02-06", "2027-02-08"),
        # A year word the customer used about something else, with the real
        # one nearer the date.
        ("今年沒空，明年 2/6-8", "2027-02-06", "2027-02-08"),
    ],
)
def test_year_words_apply_per_date_not_per_message(
    text: str, checkin: str, checkout: str
) -> None:
    result = parse_stay_dates(text, reference_date=_REF)

    assert (result.checkin_date, result.checkout_date) == (checkin, checkout)
