from app.domain.availability_probe import with_single_night_availability_probe
from app.domain.inquiry_parser import parse_inquiry


def test_single_date_probe_does_not_write_real_checkout_or_change_missing_fields() -> None:
    inquiry = parse_inquiry("8/15可以包棟嗎 9人", reference_year=2026)

    result = with_single_night_availability_probe(inquiry, inquiry.original_text)

    assert result.availability_probe_checkout == "2026-08-16"
    assert result.availability_probe_checkout_was_inferred is True
    assert result.dates.checkout_date is None
    assert result.missing_fields == ["checkout_date"]
    assert result.can_preliminarily_quote is False


def test_explicit_two_date_range_never_gets_probe_checkout() -> None:
    inquiry = parse_inquiry(
        "8/15入住 8/17退房 可以包棟嗎 9人", reference_year=2026
    )

    result = with_single_night_availability_probe(inquiry, inquiry.original_text)

    assert result.dates.checkout_date == "2026-08-17"
    assert result.availability_probe_checkout is None


def test_explicit_duration_or_compact_range_prevents_probe_inference() -> None:
    for text in ("8/15包棟兩晚 9人", "8/15包棟3天2夜 9人", "8/15到17包棟 9人"):
        inquiry = parse_inquiry(text, reference_year=2026)
        result = with_single_night_availability_probe(inquiry, text)

        assert result.availability_probe_checkout is None
        assert result.availability_probe_checkout_was_inferred is False
