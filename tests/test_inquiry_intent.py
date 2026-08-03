import pytest

from app.domain.inquiry_intent import parse_inquiry_intent


@pytest.mark.parametrize("text", ["多少錢", "價格", "價錢", "費用", "報價"])
def test_price_intent(text: str) -> None:
    result = parse_inquiry_intent(text)

    assert result.is_inquiry is True
    assert result.inquiry_type == "price"


@pytest.mark.parametrize("text", ["有房嗎", "空房", "還有房", "可訂", "有空"])
def test_availability_intent(text: str) -> None:
    result = parse_inquiry_intent(text)

    assert result.is_inquiry is True
    assert result.inquiry_type == "availability"


@pytest.mark.parametrize("text", ["我要訂房", "想預訂", "幫我保留"])
def test_booking_question_intent(text: str) -> None:
    result = parse_inquiry_intent(text)

    assert result.is_inquiry is True
    assert result.inquiry_type == "booking_question"


def test_price_is_preferred_when_price_and_availability_appear() -> None:
    result = parse_inquiry_intent("5/12還有房嗎多少錢")

    assert result.is_inquiry is True
    assert result.inquiry_type == "price"


def test_unknown_non_inquiry() -> None:
    result = parse_inquiry_intent("好的謝謝")

    assert result.is_inquiry is False
    assert result.inquiry_type == "unknown"


def test_other_question_can_be_faq() -> None:
    result = parse_inquiry_intent("可以烤肉嗎")

    assert result.is_inquiry is True
    assert result.inquiry_type == "faq"


@pytest.mark.parametrize(
    "text",
    ["12人 7/10號可以嗎?", "14人 7/10可以嗎", "7/10號可以嗎?", "12人可以嗎"],
)
def test_booking_signal_with_generic_faq_term_is_availability(text: str) -> None:
    result = parse_inquiry_intent(text)

    assert result.is_inquiry is True
    assert result.inquiry_type == "availability"


@pytest.mark.parametrize("text", ["7/10可以帶寵物嗎", "12人可以烤肉嗎"])
def test_explicit_faq_topic_with_booking_signal_stays_faq(text: str) -> None:
    result = parse_inquiry_intent(text)

    assert result.is_inquiry is True
    assert result.inquiry_type == "faq"


@pytest.mark.parametrize("text", ["可以帶寵物嗎", "有什麼設備"])
def test_pure_explicit_faq_topic_stays_faq(text: str) -> None:
    result = parse_inquiry_intent(text)

    assert result.is_inquiry is True
    assert result.inquiry_type == "faq"


def test_booking_equivalent_topic_with_booking_signals_is_availability() -> None:
    result = parse_inquiry_intent("您好,請問8/15是否還可以包棟嗎?人數9位,謝謝")

    assert result.is_inquiry is True
    assert result.inquiry_type == "availability"


def test_any_booking_equivalent_topic_wins_rule_fallback_collision() -> None:
    result = parse_inquiry_intent("8/15 包棟可以帶寵物嗎 9人")

    assert result.inquiry_type == "availability"


def test_structured_form_reply_is_booking_question_not_faq() -> None:
    text = (
        "哈囉,歡迎來枕123民宿😊\n"
        "請告知您想詢問的問題,欲訂房請提供以下資訊,有專人為您服務,謝謝。\n"
        "聯絡人:林小姐\n"
        "聯絡電話:0912345678\n"
        "入住日期:8/15\n"
        "入住人數:8位大人1位嬰兒\n"
        "是否有寵物(僅限小型寵物,每隻酌收NT500):否\n"
        "是否烤肉(酌收清潔費NT1,000):是\n"
        "幾台車:2-3台"
    )
    result = parse_inquiry_intent(text)

    assert result.is_inquiry is True
    assert result.inquiry_type == "booking_question"


def test_structured_form_reply_without_booking_keyword_is_booking_question() -> None:
    # Same shape as above but WITHOUT the "欲訂房" boilerplate line, so the
    # _BOOKING_TERMS short-circuit can't fire -- only the field-line shape
    # (label:value x3+ with a real date/guest-count signal) can catch this.
    text = (
        "聯絡人:林小姐\n"
        "聯絡電話:0912345678\n"
        "入住日期:8/15\n"
        "入住人數:8位大人1位嬰兒\n"
        "是否有寵物(僅限小型寵物,每隻酌收NT500):否\n"
        "是否烤肉(酌收清潔費NT1,000):是\n"
        "幾台車:2-3台"
    )
    result = parse_inquiry_intent(text)

    assert result.is_inquiry is True
    assert result.inquiry_type == "booking_question"
