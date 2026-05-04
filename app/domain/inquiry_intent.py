from app.domain.parser_models import InquiryIntentResult


_PRICE_TERMS = ("多少錢", "價格", "價錢", "費用", "報價")
_AVAILABILITY_TERMS = ("還有房", "有房", "空房", "可訂", "有空")
_BOOKING_TERMS = ("訂房", "預訂", "保留")
_FAQ_TERMS = ("可不可以", "能不能", "可以嗎", "能嗎", "嗎", "?", "？")
_OTHER_INQUIRY_TERMS = ("請問", "想問", "詢問", "問一下")


def parse_inquiry_intent(text: str) -> InquiryIntentResult:
    if _contains_any(text, _PRICE_TERMS):
        return InquiryIntentResult(is_inquiry=True, inquiry_type="price")

    if _contains_any(text, _AVAILABILITY_TERMS):
        return InquiryIntentResult(is_inquiry=True, inquiry_type="availability")

    if _contains_any(text, _BOOKING_TERMS):
        return InquiryIntentResult(is_inquiry=True, inquiry_type="booking_question")

    if _contains_any(text, _FAQ_TERMS):
        return InquiryIntentResult(is_inquiry=True, inquiry_type="faq")

    if _contains_any(text, _OTHER_INQUIRY_TERMS):
        return InquiryIntentResult(is_inquiry=True, inquiry_type="unknown")

    return InquiryIntentResult(is_inquiry=False, inquiry_type="unknown")


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)
