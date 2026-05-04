from app.domain.date_parser import parse_stay_dates
from app.domain.guest_count_parser import parse_guest_counts
from app.domain.inquiry_intent import parse_inquiry_intent
from app.domain.parser_models import InquiryParseResult
from app.domain.pet_parser import parse_pets


_QUOTE_RELEVANT_INTENTS = {"price", "availability", "booking_question"}


def parse_inquiry(text: str, reference_year: int | None = None) -> InquiryParseResult:
    intent = parse_inquiry_intent(text)
    dates = parse_stay_dates(text, reference_year=reference_year)
    guests = parse_guest_counts(text)
    pets = parse_pets(text)

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
