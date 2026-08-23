from datetime import datetime, timezone

from app.adapters.llm.deepseek_provider import DeepSeekProvider
from app.adapters.llm.fake_provider import FakeProvider
from app.adapters.llm import openrouter_base
from app.domain.inquiry_parser import parse_inquiry
from app.domain.llm_fallback import (
    TYPE_3_FAQ_BOOKING_COLLISION,
    TYPE_4_STATE_CONTINUATION_JUDGMENT,
    judge_state_continuation,
    llm_fallback_parse,
)
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


def test_type_3_collision_trigger_only_runs_for_topic_plus_booking_signal() -> None:
    provider = FakeProvider(_out(intent="availability", is_booking_intent=True))

    _fallback("8/15可以包棟嗎 9人", provider)
    _fallback("是包棟嗎", provider)

    assert [call["trigger"] for call in provider.calls] == [
        TYPE_3_FAQ_BOOKING_COLLISION
    ]


def test_type_3_llm_can_choose_faq_without_mutating_rule_parsed_slots() -> None:
    provider = FakeProvider(
        _out(intent="faq", intents=["faq:whole_house"], is_booking_intent=False)
    )

    result = _fallback("8/15可以包棟嗎 9人", provider)

    assert result.intent.inquiry_type == "faq"
    assert result.dates.checkin_date == "2026-08-15"
    assert result.dates.checkout_date is None
    assert result.guests.guest_count == 9
    assert result.llm_detected_intents == ["faq:whole_house"]


def test_type_3_provider_failure_keeps_topic_classification_rule_fallback() -> None:
    product = _fallback("8/15可以包棟嗎 9人", FakeProvider(None))
    policy = _fallback("7/10可以帶寵物嗎", FakeProvider(None))

    assert product.intent.inquiry_type == "availability"
    assert policy.intent.inquiry_type == "faq"


def test_type_3_llm_disabled_uses_product_policy_rule_fallback(monkeypatch) -> None:
    monkeypatch.setenv("LLM_ENABLED", "false")
    provider = FakeProvider(_out(intent="faq", is_booking_intent=False))

    product = _fallback("8/15可以包棟嗎 9人", provider)
    policy = _fallback("7/10可以帶寵物嗎", provider)

    assert provider.calls == []
    assert product.intent.inquiry_type == "availability"
    assert policy.intent.inquiry_type == "faq"


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


def test_provider_preserves_valid_multi_intents_and_drops_invalid_values(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        openrouter_base,
        "call_openrouter",
        lambda **kwargs: (
            '{"intent":"availability","intents":["availability","faq:whole_house",'
            '"faq:BAD-TOPIC",42],"checkin_date":null,"checkout_date":null,'
            '"adult_count":null,"child_count":null,"infant_count":null,'
            '"pet_count":null,"has_pet":null,"last_message_text":null,'
            '"is_booking_intent":true,"needs_clarification":false,'
            '"clarification_reason":null}'
        ),
    )
    provider = DeepSeekProvider(api_key="test-key", model="test-model")

    result = provider.parse(
        raw_text="8/15可以包棟嗎 9人",
        reference_year=2026,
        trigger=TYPE_3_FAQ_BOOKING_COLLISION,
        tenant_id=1,
    )

    assert result is not None
    assert result.intents == ["availability", "faq:whole_house"]


def test_collision_prompt_forbids_availability_pricing_reply_and_slot_extraction() -> None:
    prompt = openrouter_base._build_system_prompt(  # noqa: SLF001
        2026, TYPE_3_FAQ_BOOKING_COLLISION
    )

    assert "不要判斷實際空房" in prompt
    assert "不要計價" in prompt
    assert "不要產生客人回覆" in prompt
    assert "欄位全部填 null" in prompt


_STATE = {
    "id": 1,
    "status": "in_progress",
    "checkin_date": "2026-08-08",
    "checkout_date": "2026-08-09",
    "adult_count": 6,
    "child_count": None,
    "infant_count": None,
    "room_count": None,
    "pet_count": None,
    "has_pet": False,
    "wants_bbq": False,
}


def test_judge_state_continuation_returns_llm_verdict() -> None:
    provider = FakeProvider(_out(is_booking_intent=False))

    verdict = judge_state_continuation(
        state=_STATE,
        raw_text="謝謝你喔",
        reference_year=2026,
        tenant_id=1,
        provider=provider,
    )

    assert verdict is False
    assert provider.calls[0]["trigger"] == TYPE_4_STATE_CONTINUATION_JUDGMENT
    assert "還缺" in provider.calls[0]["raw_text"]
    assert "謝謝你喔" in provider.calls[0]["raw_text"]


def test_judge_state_continuation_returns_none_when_provider_missing() -> None:
    verdict = judge_state_continuation(
        state=_STATE,
        raw_text="謝謝你喔",
        reference_year=2026,
        tenant_id=1,
        provider=None,
    )

    assert verdict is None


def test_judge_state_continuation_returns_none_on_provider_failure() -> None:
    verdict = judge_state_continuation(
        state=_STATE,
        raw_text="謝謝你喔",
        reference_year=2026,
        tenant_id=1,
        provider=FakeProvider(None),
    )

    assert verdict is None


def test_judge_state_continuation_returns_none_when_llm_disabled(monkeypatch) -> None:
    monkeypatch.setenv("LLM_ENABLED", "false")
    provider = FakeProvider(_out(is_booking_intent=False))

    verdict = judge_state_continuation(
        state=_STATE,
        raw_text="謝謝你喔",
        reference_year=2026,
        tenant_id=1,
        provider=provider,
    )

    assert verdict is None
    assert provider.calls == []


def test_state_continuation_prompt_forbids_reply_and_slot_extraction_and_defaults_true() -> None:
    prompt = openrouter_base._build_system_prompt(  # noqa: SLF001
        2026, TYPE_4_STATE_CONTINUATION_JUDGMENT
    )

    assert "不要判斷實際空房" in prompt
    assert "不要計價" in prompt
    assert "不要產生客人回覆" in prompt
    assert "欄位全部填 null" in prompt
    assert "傾向 true" in prompt
