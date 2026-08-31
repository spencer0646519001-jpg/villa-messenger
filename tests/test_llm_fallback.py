from datetime import datetime, timezone

from app.adapters.llm.deepseek_provider import DeepSeekProvider
from app.adapters.llm.fake_provider import FakeProvider
from app.adapters.llm import openrouter_base
from app.domain.inquiry_parser import parse_inquiry
from app.domain.llm_fallback import (
    TYPE_3_FAQ_BOOKING_COLLISION,
    TYPE_4_STATE_CONTINUATION_JUDGMENT,
    TYPE_5_BBQ_AMBIGUITY,
    TYPE_6_UNCLASSIFIED_INQUIRY,
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
    # "下週五到下週日" (relative dates) still needs LLM translation, unlike
    # "7/28-29", which inquiry_intent's own date_parser now resolves directly
    # (see the M/D-D shorthand fix -- eval control_16 etc regression).
    provider = FakeProvider(
        outputs_by_text={
            "下週五到下週日多少錢": _out(
                intent="price",
                checkin_date="2026-05-08",
                checkout_date="2026-05-10",
            ),
            "28入住29退房多少錢": _out(
                intent="price",
                checkin_date="2026-05-28",
                checkout_date="2026-05-29",
            ),
        }
    )

    first = _fallback("下週五到下週日多少錢", provider)
    second = _fallback("28入住29退房多少錢", provider)

    assert first.dates.checkin_date == "2026-05-08"
    assert first.dates.checkout_date == "2026-05-10"
    assert second.dates.checkin_date == "2026-05-28"
    assert second.dates.checkout_date == "2026-05-29"
    assert [call["trigger"] for call in provider.calls] == [
        "type_1_date_translation",
        "type_1_date_translation",
    ]


def test_type_1_date_translation_with_explicit_rejection_keeps_dates_unresolved() -> None:
    # Codex review of commit fceb69e (P2): TYPE_1 can also return resolved
    # dates alongside an explicit is_booking_intent=False (e.g. a business
    # meeting, not a stay) -- trusting those dates as booking slots would be
    # exactly as wrong as trusting a TYPE_2 rejection's slots.
    provider = FakeProvider(
        _out(
            intent="other",
            checkin_date="2026-05-08",
            checkout_date="2026-05-10",
            is_booking_intent=False,
        )
    )

    result = _fallback("會議安排下週五到下週日", provider)

    assert result.dates.checkin_date is None
    assert result.dates.checkout_date is None
    assert result.intent.inquiry_type == "unknown"
    assert result.llm_rejected_booking_intent is True


def test_type_1_rejection_downgrades_an_already_quote_relevant_rule_intent() -> None:
    # Codex review of commit fd603d6 (P2): TYPE_1 can fire even when the rule
    # parser ALREADY classified the message as quote-relevant (dates just
    # aren't complete yet), e.g. "下週股票多少錢" (a stock-price question,
    # not lodging) rule-classifies as "price" via the 多少錢 keyword. Without
    # downgrading intent here too, the message would still sail into the
    # booking flow and open conversation state despite the LLM's explicit
    # rejection -- setting the flag alone (as commits 0027fec/fceb69e did)
    # isn't enough when the rule intent was already quote-relevant.
    provider = FakeProvider(
        _out(intent="other", is_booking_intent=False)
    )

    result = _fallback("下週股票多少錢", provider)

    assert result.intent.is_inquiry is False
    assert result.intent.inquiry_type == "unknown"
    assert result.llm_rejected_booking_intent is True
    # Codex review of commit 5565677 (P2): missing_fields/can_preliminarily_
    # quote are quote-only concepts -- must not keep the rule parser's stale
    # quote-relevant values after intent is downgraded to non-inquiry.
    assert result.missing_fields == []
    assert result.can_preliminarily_quote is False


def test_type_2_intent_judgment_upgrades_booking_intent() -> None:
    provider = FakeProvider(
        _out(
            intent="booking",
            checkin_date="2026-03-15",
            checkout_date="2026-03-17",
            is_booking_intent=True,
        )
    )

    # No 入住/退房 labels: stays ambiguous at the rule layer (unlike
    # "3/15入住3/17退房", now resolved directly to booking_question by
    # inquiry_intent's labeled-date-range rule -- see eval failure_161 etc.)
    # so TYPE_2_INTENT_JUDGMENT is still the one to disambiguate it.
    result = _fallback("3/15到3/17", provider)

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
    inquiry = parse_inquiry("下週五到下週日多少錢", reference_year=2026)

    result = llm_fallback_parse(
        inquiry,
        "下週五到下週日多少錢",
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

    # No 入住/退房 labels -- see comment in the sibling TYPE_2 test above.
    result = _fallback("3/15到3/17", provider)

    assert result.intent.is_inquiry is False
    assert result.intent.inquiry_type == "unknown"
    assert result.dates.checkin_date == "2026-03-15"
    assert result.dates.checkout_date == "2026-03-17"
    # Codex review of commit 0027fec (P2): intent staying "unknown" here is
    # indistinguishable from an unjudged case unless this flag is set --
    # ConversationStateService's date-range OPEN bypass relies on it.
    assert result.llm_rejected_booking_intent is True


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


def test_type_3_merges_llm_provided_slots_when_present() -> None:
    # Spencer's request when expanding LLM-trigger scope: TYPE_3 already
    # pays for an LLM call to resolve the FAQ/booking classification
    # conflict, so use the same call's slot extraction instead of
    # discarding it (previously the prompt told the LLM to leave every
    # slot null on this trigger, so there was nothing to merge at all).
    provider = FakeProvider(
        _out(intent="availability", is_booking_intent=True, has_pet=True, pet_count=2)
    )

    result = _fallback("8/15可以包棟嗎 9人", provider)

    assert result.pets.has_pet is True
    assert result.pets.pet_count == 2


def test_type_5_triggers_when_bbq_mentioned_but_unresolved() -> None:
    # "他們說烤肉不錯誒" ("they said BBQ is pretty good") -- a genuine relay
    # of someone else's opinion, not a request or a policy question. Even
    # after hardening bbq_parser.py across many review rounds, this kind of
    # open-ended phrasing has no natural regex stopping point, which is
    # exactly the class of problem TYPE_5 hands off to the LLM instead.
    provider = FakeProvider(_out(wants_bbq=True))

    result = _fallback("他們說烤肉不錯誒", provider)

    assert [call["trigger"] for call in provider.calls] == [TYPE_5_BBQ_AMBIGUITY]
    assert result.bbq.wants_bbq is True
    assert result.bbq.mentioned is True


def test_type_5_does_not_trigger_when_rule_parser_already_resolved_bbq() -> None:
    provider = FakeProvider(_out(wants_bbq=False))

    _fallback("想加烤肉", provider)

    assert provider.calls == []


def test_type_5_null_wants_bbq_leaves_rule_result_untouched() -> None:
    # Tri-state contract, same as pets: None means "the LLM couldn't tell
    # either" (e.g. a pure pricing/policy question), and must NOT clobber
    # the rule parser's own wants_bbq=False/mentioned=False.
    provider = FakeProvider(_out(wants_bbq=None))

    result = _fallback("他們說烤肉不錯誒", provider)

    assert result.bbq.wants_bbq is False
    assert result.bbq.mentioned is False


def test_type_5_bbq_verdict_survives_a_booking_intent_rejection() -> None:
    # Codex review (P1): a valid TYPE_5 response can pair an explicit BBQ
    # verdict with is_booking_intent=False (the LLM decided THIS message
    # alone doesn't establish overall booking intent). Unlike TYPE_1/2's
    # dates/guests -- genuinely untrustworthy if this isn't a booking at
    # all -- the BBQ verdict is a direct answer to a direct question and
    # must survive the rejection path, or an explicit decline could fail to
    # clear a stale wants_bbq=True from an earlier turn.
    provider = FakeProvider(_out(wants_bbq=False, is_booking_intent=False))

    result = _fallback("他們說烤肉不錯誒", provider)

    assert result.bbq.wants_bbq is False
    assert result.bbq.mentioned is True
    assert result.llm_rejected_booking_intent is True


def test_type_6_triggers_on_genuinely_unclassified_inquiry() -> None:
    # "請問你們家在哪裡" contains "請問" (so the rule parser marks it as an
    # inquiry) but matches no price/availability/booking/FAQ keyword at all
    # -- the ONLY path in inquiry_intent.py that produces
    # is_inquiry=True + inquiry_type="unknown".
    provider = FakeProvider(_out(intent="faq"))

    result = _fallback("請問你們家在哪裡", provider)

    assert [call["trigger"] for call in provider.calls] == [TYPE_6_UNCLASSIFIED_INQUIRY]
    # Codex review (P2): _maybe_upgrade_intent only mapped the three
    # quote-relevant intents, so a correct LLM "faq" classification never
    # got applied and the message stayed "unknown" -- silently dropped
    # (owner push only, no customer reply) instead of getting the FAQ
    # confirm-and-defer reply conversation_reply_composer._is_faq gates on.
    assert result.intent.inquiry_type == "faq"
    assert result.intent.is_inquiry is True


def test_type_6_faq_classification_survives_an_explicit_non_booking_verdict() -> None:
    # Codex review (P2, second pass): the first fix only covered
    # is_booking_intent left null (as in the test above) -- but a coherent
    # LLM response naturally pairs intent="faq" with the EXPLICIT
    # is_booking_intent=False, which the rejection branch would otherwise
    # return through first, leaving the message "unknown" regardless of
    # the correct faq classification.
    provider = FakeProvider(_out(intent="faq", is_booking_intent=False))

    result = _fallback("請問你們家在哪裡", provider)

    assert result.intent.inquiry_type == "faq"
    assert result.intent.is_inquiry is True
    # Codex review (P2, third pass): the flag's contract is "the LLM
    # explicitly said this isn't a booking", independent of the intent
    # classification we then apply -- must still be recorded here, or
    # InquiryService's log payload reports no rejection despite the
    # provider giving one.
    assert result.llm_rejected_booking_intent is True


def test_type_6_faq_without_explicit_rejection_does_not_set_the_flag() -> None:
    # Companion to the test above -- the flag must reflect what the LLM
    # actually said, not just "TYPE_6 resulted in faq". A null
    # is_booking_intent means the LLM never made that judgment at all.
    provider = FakeProvider(_out(intent="faq"))

    result = _fallback("請問你們家在哪裡", provider)

    assert result.intent.inquiry_type == "faq"
    assert result.llm_rejected_booking_intent is False


def test_type_6_does_not_trigger_for_non_inquiry_chitchat() -> None:
    # "你好"/"謝謝" are is_inquiry=False entirely (not "unknown") -- must
    # never reach TYPE_6, or every greeting would burn an LLM call.
    provider = FakeProvider(_out(intent="other"))

    for text in ("你好", "謝謝"):
        _fallback(text, provider)

    assert provider.calls == []


def test_type_6_llm_upgrades_intent_and_extracts_slots() -> None:
    provider = FakeProvider(
        _out(intent="availability", is_booking_intent=True, checkin_date="2026-08-10")
    )

    result = _fallback("請問你們家在哪裡", provider)

    assert result.intent.inquiry_type == "availability"
    assert result.dates.checkin_date == "2026-08-10"


def test_llm_enabled_false_short_circuits_provider(monkeypatch) -> None:
    monkeypatch.setenv("LLM_ENABLED", "false")
    provider = FakeProvider(_out(checkin_date="2026-05-08", checkout_date="2026-05-10"))

    result = _fallback("下週五到下週日多少錢", provider)

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


def test_collision_prompt_forbids_availability_pricing_and_reply_but_extracts_slots() -> None:
    # Old assertion: "欄位全部填 null" (this trigger extracted nothing).
    # New assertion: the prompt now tells the LLM to extract slots normally.
    # Why: Spencer's explicit request when expanding LLM-trigger scope --
    # TYPE_3 already pays for an LLM call to resolve the FAQ/booking
    # classification conflict; discarding whatever that same call could also
    # tell us about dates/guests/pets/bbq wasted it. The availability/
    # pricing/reply-text prohibitions are unrelated to slot extraction and
    # stay exactly as strict as before.
    prompt = openrouter_base._build_system_prompt(  # noqa: SLF001
        2026, TYPE_3_FAQ_BOOKING_COLLISION
    )

    assert "不要判斷實際空房" in prompt
    assert "不要計價" in prompt
    assert "不要產生客人回覆" in prompt
    assert "同時正常抽取日期、人數、寵物、烤肉等欄位" in prompt


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
