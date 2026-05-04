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
