from app.domain.date_parser import parse_stay_dates
from app.domain.faq_matcher import (
    FaqMatch,
    is_booking_equivalent_topic,
    match_all_faq_topics,
    match_faq,
)
from app.domain.form_reply_detector import looks_like_structured_form_reply
from app.domain.guest_count_parser import parse_guest_counts
from app.domain.parser_models import InquiryIntentResult
from app.domain.text_normalizer import normalize_for_parsing


_PRICE_TERMS = ("多少錢", "價格", "價錢", "費用", "報價")
_AVAILABILITY_TERMS = ("還有房", "有房", "空房", "可訂", "有空")
_BOOKING_TERMS = ("訂房", "預訂", "保留")
_FAQ_TERMS = ("可不可以", "能不能", "可以嗎", "能嗎", "嗎", "?", "？")
_OTHER_INQUIRY_TERMS = ("請問", "想問", "詢問", "問一下")
# A leading "/" marks internal command syntax (e.g. "/紀錄", "/狀態") rather
# than customer text -- eval candidate_19/candidate_20 regression.
_COMMAND_PREFIX = "/"


def parse_inquiry_intent(text: str) -> InquiryIntentResult:
    text = normalize_for_parsing(text)

    if text.startswith(_COMMAND_PREFIX):
        return InquiryIntentResult(is_inquiry=False, inquiry_type="non_inquiry")

    if _contains_any(text, _PRICE_TERMS):
        return InquiryIntentResult(is_inquiry=True, inquiry_type="price")

    if _contains_any(text, _AVAILABILITY_TERMS):
        return InquiryIntentResult(is_inquiry=True, inquiry_type="availability")

    if _contains_any(text, _BOOKING_TERMS):
        return InquiryIntentResult(is_inquiry=True, inquiry_type="booking_question")

    if looks_like_structured_form_reply(text):
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

    # A bare, EXPLICITLY-LABELED full date range ("8/10入住 8/11退房") with no
    # other keyword still states real booking-relevant content -- eval
    # failure_161/control_427/failure_404/failure_682 regression: this used
    # to fall all the way through to the unknown/not-an-inquiry fallback
    # below. Requiring the literal 入住/退房 labels (not just two bare dates)
    # keeps this narrow: an unlabeled two-date message stays ambiguous and is
    # still left for the LLM's TYPE_2_INTENT_JUDGMENT fallback to judge.
    if _has_labeled_full_date_range(text):
        return InquiryIntentResult(is_inquiry=True, inquiry_type="booking_question")

    return InquiryIntentResult(is_inquiry=False, inquiry_type="unknown")


def has_explicit_booking_term(text: str) -> bool:
    """True when text contains an explicit booking-intent keyword (訂房/預訂/
    保留). Exposed for conversation_reply_composer's booking-context check,
    which needs to distinguish an explicit booking request ("9/20入住,想訂房
    也想烤肉,總共多少錢") from a bare dated ancillary-fee question ("9/20 停車
    要多少錢") once 多少錢 has already won parse_inquiry_intent's priority
    race and no guest count was given either."""
    return _contains_any(normalize_for_parsing(text), _BOOKING_TERMS)


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _has_booking_signal(text: str) -> bool:
    return _has_date_signal(text) or parse_guest_counts(text).guest_count is not None


def _has_date_signal(text: str) -> bool:
    dates = parse_stay_dates(text)
    return dates.checkin_date is not None or dates.checkout_date is not None


def _has_labeled_full_date_range(text: str) -> bool:
    if "入住" not in text or "退房" not in text:
        return False
    dates = parse_stay_dates(text)
    return dates.checkin_date is not None and dates.checkout_date is not None


def _is_checkout_date_label(faq_match: FaqMatch, text: str) -> bool:
    return faq_match.topic == "checkout" and _has_date_signal(text)
