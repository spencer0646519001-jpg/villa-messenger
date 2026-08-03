from app.domain.inquiry_parser import parse_inquiry


def test_parse_complete_price_inquiry_with_pet() -> None:
    result = parse_inquiry("5/12入住 5/14退房 2大1小1毛孩多少錢", reference_year=2026)

    assert result.original_text == "5/12入住 5/14退房 2大1小1毛孩多少錢"
    assert result.intent.inquiry_type == "price"
    assert result.dates.checkin_date == "2026-05-12"
    assert result.dates.checkout_date == "2026-05-14"
    assert result.dates.nights == 2
    assert result.guests.adult_count == 2
    assert result.guests.child_count == 1
    assert result.guests.guest_count == 3
    assert result.pets.has_pet is True
    assert result.pets.pet_count == 1
    assert result.pets.pet_type == "dog"
    assert result.missing_fields == []
    assert result.can_preliminarily_quote is True


def test_parse_complete_unlabeled_date_price_inquiry_with_pet() -> None:
    result = parse_inquiry("5/12 5/14 2大1小1毛孩多少錢", reference_year=2026)

    assert result.intent.inquiry_type == "price"
    assert result.dates.checkin_date == "2026-05-12"
    assert result.dates.checkout_date == "2026-05-14"
    assert result.dates.nights == 2
    assert result.guests.adult_count == 2
    assert result.guests.child_count == 1
    assert result.guests.guest_count == 3
    assert result.pets.has_pet is True
    assert result.pets.pet_count == 1
    assert result.pets.pet_type == "dog"
    assert result.can_preliminarily_quote is True


def test_parse_vague_date_price_inquiry_with_guest_count() -> None:
    result = parse_inquiry("請問暑假四個人多少錢", reference_year=2026)

    assert result.intent.inquiry_type == "price"
    assert result.guests.guest_count == 4
    assert result.dates.checkin_date is None
    assert result.dates.checkout_date is None
    assert result.missing_fields == ["checkin_date", "checkout_date"]
    assert result.can_preliminarily_quote is False


def test_parse_single_date_inquiry_with_infant() -> None:
    result = parse_inquiry("5/12 2大1嬰兒多少錢", reference_year=2026)

    assert result.intent.inquiry_type == "price"
    assert result.dates.checkin_date == "2026-05-12"
    assert result.dates.checkout_date is None
    assert result.guests.adult_count == 2
    assert result.guests.infant_count == 1
    assert result.guests.guest_count == 2
    assert result.guests.needs_infant_confirmation is True
    assert result.missing_fields == ["checkout_date"]
    assert result.can_preliminarily_quote is False


def test_pet_count_is_required_when_pet_is_mentioned() -> None:
    result = parse_inquiry("5/12入住 5/14退房 四人帶狗多少錢", reference_year=2026)

    assert result.pets.has_pet is True
    assert result.pets.pet_count is None
    assert result.missing_fields == ["pet_count"]
    assert result.can_preliminarily_quote is False


# ---------- full-width IME input (NFKC normalization) ----------


def test_full_width_slash_date_parses() -> None:
    # The reported live bug: full-width slash "6／14" parsed to null before NFKC.
    result = parse_inquiry("6／14有房嗎", reference_year=2026)

    assert result.dates.checkin_date == "2026-06-14"


def test_full_width_digits_date_parses() -> None:
    result = parse_inquiry("６／１４有房嗎", reference_year=2026)

    assert result.dates.checkin_date == "2026-06-14"


def test_full_width_space_date_variant_parses() -> None:
    # Full-width space (U+3000) between date and label must not block matching.
    result = parse_inquiry("5／12　入住　5／14　退房", reference_year=2026)

    assert result.dates.checkin_date == "2026-05-12"
    assert result.dates.checkout_date == "2026-05-14"
    assert result.dates.nights == 2


def test_full_width_digit_guest_count_parses() -> None:
    result = parse_inquiry("２大人", reference_year=2026)

    assert result.guests.adult_count == 2


def test_half_width_slash_date_still_parses() -> None:
    # Regression guard: NFKC is a no-op on half-width input.
    result = parse_inquiry("6/14有房嗎", reference_year=2026)

    assert result.dates.checkin_date == "2026-06-14"


def test_original_text_keeps_unnormalized_full_width_form() -> None:
    # We parse the normalized copy but STORE the customer's original text.
    result = parse_inquiry("6／14有房嗎", reference_year=2026)

    assert result.original_text == "6／14有房嗎"
    assert result.dates.checkin_date == "2026-06-14"


def test_room_count_is_parsed_but_not_a_missing_field() -> None:
    result = parse_inquiry("7/28入住 7/29退房 13大人 開4房多少錢", reference_year=2026)

    assert result.room_count == 4
    assert "room_count" not in result.missing_fields


def test_booking_signal_generic_question_enters_quote_chain() -> None:
    result = parse_inquiry("12人 7/10號可以嗎?", reference_year=2026)

    assert result.intent.inquiry_type == "availability"
    assert result.dates.checkin_date == "2026-07-10"
    assert result.guests.guest_count == 12
    assert result.guests.adult_count == 12
    assert result.missing_fields == ["checkout_date"]


def test_faq_topic_with_booking_signal_does_not_enter_quote_chain() -> None:
    result = parse_inquiry("7/10可以帶寵物嗎", reference_year=2026)

    assert result.intent.inquiry_type == "faq"
    assert result.dates.checkin_date == "2026-07-10"
    assert result.missing_fields == []


def test_parser_records_all_matched_faq_topics_in_stable_order() -> None:
    result = parse_inquiry("8/15可以帶寵物包棟嗎 9人", reference_year=2026)

    assert result.matched_faq_topics == ["pets", "whole_house"]
    assert result.intent.inquiry_type == "availability"


def test_parse_complete_price_inquiry_with_bbq() -> None:
    result = parse_inquiry("5/12入住 5/14退房 4大要烤肉多少錢", reference_year=2026)

    assert result.bbq.wants_bbq is True


def test_parse_inquiry_defaults_bbq_false_when_not_mentioned() -> None:
    result = parse_inquiry("5/12入住 5/14退房 4大多少錢", reference_year=2026)

    assert result.bbq.wants_bbq is False
