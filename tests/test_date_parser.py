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
