from app.domain.date_parser import parse_stay_dates
from app.domain.guest_count_parser import parse_guest_counts
from app.domain.inquiry_intent import parse_inquiry_intent
from app.domain.parser_models import InquiryParseResult
from app.domain.pet_parser import parse_pets
from app.domain.text_normalizer import normalize_for_parsing


_QUOTE_RELEVANT_INTENTS = {"price", "availability", "booking_question"}


def parse_inquiry(text: str, reference_year: int | None = None) -> InquiryParseResult:
    # Normalize ONCE here, before every sub-parser, so full-width IME input
    # (／ ６ １４ 　) matches the half-width-assuming regexes. original_text below
    # keeps the UN-normalized `text` so the stored record / owner push preserve
    # exactly what the customer typed.
    normalized = normalize_for_parsing(text)
    intent = parse_inquiry_intent(normalized)
    dates = parse_stay_dates(normalized, reference_year=reference_year)
    guests = parse_guest_counts(normalized)
    pets = parse_pets(normalized)

    missing_fields = []
    if intent.inquiry_type in _QUOTE_RELEVANT_INTENTS:
        if dates.checkin_date is None:
            missing_fields.append("checkin_date")
        if dates.checkout_date is None:
            missing_fields.append("checkout_date")
        if guests.guest_count is None:
            missing_fields.append("guest_count")
        if pets.has_pet and pets.pet_count is None:
            missing_fields.append("pet_count")

    can_preliminarily_quote = (
        intent.inquiry_type in _QUOTE_RELEVANT_INTENTS
        and dates.checkin_date is not None
        and dates.checkout_date is not None
        and dates.nights is not None
        and dates.nights > 0
        and guests.guest_count is not None
        and (not pets.has_pet or pets.pet_count is not None)
    )

    return InquiryParseResult(
        original_text=text,
        intent=intent,
        dates=dates,
        guests=guests,
        pets=pets,
        missing_fields=missing_fields,
        can_preliminarily_quote=can_preliminarily_quote,
    )
