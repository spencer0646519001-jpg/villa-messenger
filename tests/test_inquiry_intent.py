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
