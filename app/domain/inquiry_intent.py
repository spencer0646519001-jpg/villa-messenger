from app.domain.date_parser import parse_stay_dates
from app.domain.faq_matcher import (
    FaqMatch,
    is_booking_equivalent_topic,
    match_all_faq_topics,
    match_faq,
)
from app.domain.guest_count_parser import parse_guest_counts
from app.domain.parser_models import InquiryIntentResult
from app.domain.text_normalizer import normalize_for_parsing


_PRICE_TERMS = ("多少錢", "價格", "價錢", "費用", "報價")
_AVAILABILITY_TERMS = ("還有房", "有房", "空房", "可訂", "有空")
_BOOKING_TERMS = ("訂房", "預訂", "保留")
_FAQ_TERMS = ("可不可以", "能不能", "可以嗎", "能嗎", "嗎", "?", "？")
_OTHER_INQUIRY_TERMS = ("請問", "想問", "詢問", "問一下")


def parse_inquiry_intent(text: str) -> InquiryIntentResult:
    text = normalize_for_parsing(text)

    if _contains_any(text, _PRICE_TERMS):
        return InquiryIntentResult(is_inquiry=True, inquiry_type="price")

    if _contains_any(text, _AVAILABILITY_TERMS):
        return InquiryIntentResult(is_inquiry=True, inquiry_type="availability")

    if _contains_any(text, _BOOKING_TERMS):
        return InquiryIntentResult(is_inquiry=True, inquiry_type="booking_question")

    faq_matches = match_all_faq_topics(text)
    faq_match = faq_matches[0] if faq_matches else None
    if faq_match is not None and not _is_checkout_date_label(faq_match, text):
        if _has_booking_signal(text) and any(
            is_booking_equivalent_topic(match.topic) for match in faq_matches
        ):
            return InquiryIntentResult(is_inquiry=True, inquiry_type="availability")
        return InquiryIntentResult(is_inquiry=True, inquiry_type="faq")

    if _contains_any(text, _FAQ_TERMS) and _has_booking_signal(text):
        return InquiryIntentResult(is_inquiry=True, inquiry_type="availability")

    if _contains_any(text, _FAQ_TERMS):
        return InquiryIntentResult(is_inquiry=True, inquiry_type="faq")

    if _contains_any(text, _OTHER_INQUIRY_TERMS):
        return InquiryIntentResult(is_inquiry=True, inquiry_type="unknown")

    return InquiryIntentResult(is_inquiry=False, inquiry_type="unknown")


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _has_booking_signal(text: str) -> bool:
    return _has_date_signal(text) or parse_guest_counts(text).guest_count is not None


def _has_date_signal(text: str) -> bool:
    dates = parse_stay_dates(text)
    return dates.checkin_date is not None or dates.checkout_date is not None


def _is_checkout_date_label(faq_match: FaqMatch, text: str) -> bool:
    return faq_match.topic == "checkout" and _has_date_signal(text)
