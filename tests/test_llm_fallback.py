from datetime import datetime, timezone

from app.adapters.llm.deepseek_provider import DeepSeekProvider
from app.adapters.llm.fake_provider import FakeProvider
from app.adapters.llm import openrouter_base
from app.domain.inquiry_parser import parse_inquiry
from app.domain.llm_fallback import llm_fallback_parse
from app.domain.llm_provider import LLMOutput
from app.domain.reply_text import DATE_RANGE_CLARIFICATION_MESSAGE
from app.schemas import InboundMessage
from app.services.inquiry_service import InquiryService


_QUOTE_RELEVANT_INTENTS = {"price", "availability", "booking_question"}


class _FakeOperationModeService:
    def is_system_active(self, *, tenant_id: int, tenant_timezone: str) -> bool:
        return True


def _out(**overrides: object) -> LLMOutput:
    values = {
        "intent": None,
        "checkin_date": None,
        "checkout_date": None,
        "adult_count": None,
        "child_count": None,
        "infant_count": None,
        "pet_count": None,
        "has_pet": None,
        "last_message_text": None,
        "is_booking_intent": None,
        "needs_clarification": False,
        "clarification_reason": None,
    }
    values.update(overrides)
    return LLMOutput(**values)


def _quote_relevant(inquiry) -> bool:
    return inquiry.intent.is_inquiry and inquiry.intent.inquiry_type in _QUOTE_RELEVANT_INTENTS


def _fallback(text: str, provider: FakeProvider):
    inquiry = parse_inquiry(text, reference_year=2026)
    return llm_fallback_parse(
        inquiry,
        text,
        reference_year=2026,
        is_quote_relevant=_quote_relevant(inquiry),
        tenant_id=1,
        provider=provider,
    )


def _message(text: str) -> InboundMessage:
    return InboundMessage(
        tenant_id=1,
        tenant_slug="zhen123-house",
        tenant_timezone="Asia/Taipei",
        platform="line",
        platform_user_id="Uguest",
        text=text,
        timestamp=datetime(2026, 5, 1, 1, 0, tzinfo=timezone.utc),
    )


def test_gate_does_not_call_provider_for_non_fallback_inputs() -> None:
    provider = FakeProvider(_out(checkin_date="2026-01-01"))

    for text in ("5/12入住5/14退房多少錢", "你好在嗎", "6／14有房嗎"):
        _fallback(text, provider)

    assert provider.calls == []


def test_type_1_date_translation_fills_short_range_dates() -> None:
    provider = FakeProvider(
        outputs_by_text={
            "7/28-29多少錢": _out(
                intent="price",
                checkin_date="2026-07-28",
                checkout_date="2026-07-29",
            ),
            "28入住29退房多少錢": _out(
                intent="price",
                checkin_date="2026-05-28",
                checkout_date="2026-05-29",
            ),
        }
    )

    first = _fallback("7/28-29多少錢", provider)
    second = _fallback("28入住29退房多少錢", provider)

    assert first.dates.checkin_date == "2026-07-28"
    assert first.dates.checkout_date == "2026-07-29"
    assert second.dates.checkin_date == "2026-05-28"
    assert second.dates.checkout_date == "2026-05-29"
    assert [call["trigger"] for call in provider.calls] == [
        "type_1_date_translation",
        "type_1_date_translation",
    ]


def test_type_2_intent_judgment_upgrades_booking_intent() -> None:
    provider = FakeProvider(
        _out(
            intent="booking",
            checkin_date="2026-03-15",
            checkout_date="2026-03-17",
            is_booking_intent=True,
        )
    )

    result = _fallback("3/15入住3/17退房", provider)

    assert provider.calls[0]["trigger"] == "type_2_intent_judgment"
    assert result.intent.is_inquiry is True
    assert result.intent.inquiry_type == "booking_question"
    assert _quote_relevant(result) is True


def test_case_2_service_returns_local_date_range_clarification_template() -> None:
    provider = FakeProvider(
        _out(
            intent="booking",
            is_booking_intent=True,
            needs_clarification=True,
            clarification_reason="date_range_too_broad",
        )
    )
    service = InquiryService(
        operation_mode_service=_FakeOperationModeService(),
        tenant_pricing_loader=lambda tenant_id: {},
        tenant_special_dates_loader=lambda tenant_id: {},
        llm_provider=provider,
        now_provider=lambda: datetime(2026, 5, 1, tzinfo=timezone.utc),
    )

    decision = service.handle_message(message=_message("下個月想訂"))

    assert decision.action_type == "reply_to_customer_only"
    assert decision.customer_reply_text == DATE_RANGE_CLARIFICATION_MESSAGE
    assert decision.log_payload["action_taken"] == "missing_info"


def test_case_3_provider_failure_returns_rule_result_unchanged() -> None:
    provider = FakeProvider(None)
    inquiry = parse_inquiry("7/28-29多少錢", reference_year=2026)

    result = llm_fallback_parse(
        inquiry,
        "7/28-29多少錢",
        reference_year=2026,
        is_quote_relevant=_quote_relevant(inquiry),
        tenant_id=1,
        provider=provider,
    )

    assert result is inquiry
    assert provider.calls[0]["trigger"] == "type_1_date_translation"


def test_type_2_non_booking_does_not_upgrade_or_mutate_slots() -> None:
    provider = FakeProvider(
        _out(
            intent="other",
            checkin_date="2026-04-01",
            checkout_date="2026-04-02",
            is_booking_intent=False,
        )
    )

    result = _fallback("3/15入住3/17退房", provider)

    assert result.intent.is_inquiry is False
    assert result.intent.inquiry_type == "unknown"
    assert result.dates.checkin_date == "2026-03-15"
    assert result.dates.checkout_date == "2026-03-17"


def test_llm_enabled_false_short_circuits_provider(monkeypatch) -> None:
    monkeypatch.setenv("LLM_ENABLED", "false")
    provider = FakeProvider(_out(checkin_date="2026-07-28", checkout_date="2026-07-29"))

    result = _fallback("7/28-29多少錢", provider)

    assert provider.calls == []
    assert result.dates.checkout_date is None


def test_provider_bad_json_returns_none(monkeypatch) -> None:
    monkeypatch.setattr(openrouter_base, "call_openrouter", lambda **kwargs: "{bad json")
    provider = DeepSeekProvider(api_key="test-key", model="test-model")

    result = provider.parse(
        raw_text="7/28-29",
        reference_year=2026,
        trigger="type_1_date_translation",
        tenant_id=1,
    )

    assert result is None


def test_provider_clears_dates_that_do_not_match_iso_format(monkeypatch) -> None:
    monkeypatch.setattr(
        openrouter_base,
        "call_openrouter",
        lambda **kwargs: (
            '{"intent":"price","checkin_date":"7/28","checkout_date":"2026-07-29",'
            '"adult_count":null,"child_count":null,"infant_count":null,'
            '"pet_count":null,"has_pet":null,"last_message_text":null,'
            '"is_booking_intent":null,"needs_clarification":false,'
            '"clarification_reason":null}'
        ),
    )
    provider = DeepSeekProvider(api_key="test-key", model="test-model")

    result = provider.parse(
        raw_text="7/28-29",
        reference_year=2026,
        trigger="type_1_date_translation",
        tenant_id=1,
    )

    assert result is not None
    assert result.checkin_date is None
    assert result.checkout_date == "2026-07-29"
