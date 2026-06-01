"""
Unit tests for ConversationReplyComposer (STAGE C reply selection), exercised in
isolation with fake pricing loaders and hand-built state rows / decisions.

Covers the four branches of compose():
  - no active state / off mode / urgent -> the per-message reply (fallback)
  - active + incomplete -> missing-slot prompt (from the accumulated state)
  - active + complete -> quote from accumulated slots + completed_state_id
  - active + complete-but-unquotable (over capacity) -> the over-capacity reply
"""

from datetime import date

import pytest

from app.domain.inquiry_decision import InquiryDecision
from app.domain.pricing_policy import calculate_price
from app.domain.reply_templates import (
    render_faq_breakfast,
    render_faq_checkout,
    render_faq_pets,
    render_over_capacity_message,
    render_quote_message,
)
from app.domain.reply_text import (
    FAQ_PARKING_LEAD,
    FAQ_WIFI_LEAD,
    SINGLE_MISSING_GUEST_COUNT_MESSAGE,
)
from app.schemas import InboundMessage
from app.services.conversation_reply_composer import (
    ComposedReply,
    ConversationReplyComposer,
)

_PRICING = {
    "base_prices_per_night": {
        "8_people": {"weekday": 9000, "saturday": 15000, "summer_weekday": 12000,
                     "summer_saturday_or_holiday": 15000, "spring_festival": 25000},
    },
    "pets": {
        "allowed_with_notice": True,
        "small_dogs_only_for_now": True,
        "fee_twd_per_pet_per_stay": 500,
    },
}

_STAY_POLICY = {
    "breakfast_provided": False,
    "check_in_after": "15:00",
    "checkout_before": "11:00",
}


def _composer() -> ConversationReplyComposer:
    return ConversationReplyComposer(
        tenant_pricing_loader=lambda tid: _PRICING,
        tenant_special_dates_loader=lambda tid: {},
        tenant_stay_policy_loader=lambda tid: _STAY_POLICY,
    )


def _message(text: str = "x") -> InboundMessage:
    return InboundMessage(
        tenant_id=1, tenant_slug="t", tenant_timezone="Asia/Taipei",
        platform="line", platform_user_id="Uguest", text=text,
    )


def _faq_decision() -> InquiryDecision:
    """A faq-intent message reaches the composer as the non-inquiry push-owner
    decision (no customer reply), carrying inquiry_intent='faq' in the log."""
    return InquiryDecision(
        action_type="push_to_owner_only", owner_push_text="(owner)",
        log_payload={"inquiry_intent": "faq"}, parsed_as_inquiry=True,
    )


def _price_intent_decision() -> InquiryDecision:
    """Simulates what inquiry_service returns for a price-intent message
    (e.g. '早餐多少錢嗎' classifies as 'price' but also hits a NON_PRICEABLE
    topic — the composer override should route it to FAQ regardless)."""
    return InquiryDecision(
        action_type="reply_to_customer_only",
        customer_reply_text="PER_MESSAGE_PRICE_REPLY",
        log_payload={"inquiry_intent": "price"},
        parsed_as_inquiry=True,
    )


def _decision(*, off: bool = False, urgent: bool = False) -> InquiryDecision:
    if urgent:
        return InquiryDecision(
            action_type="push_owner_urgent", owner_push_text="!", was_urgent=True,
            log_payload={"a": 1},
        )
    if off:
        return InquiryDecision(
            action_type="do_nothing", was_system_off=True, log_payload={"a": 1},
        )
    return InquiryDecision(
        action_type="reply_to_customer_only", customer_reply_text="PER_MESSAGE",
        log_payload={"a": 1},
    )


def _state(**overrides) -> dict:
    base = {
        "id": 7, "status": "in_progress",
        "checkin_date": None, "checkout_date": None,
        "adult_count": None, "child_count": None, "infant_count": None,
        "pet_count": None, "has_pet": 0,
    }
    base.update(overrides)
    return base


def test_no_active_state_returns_per_message_reply() -> None:
    result = _composer().compose(message=_message(), decision=_decision(), state=None)
    assert result == ComposedReply(text="PER_MESSAGE")


def test_off_mode_returns_per_message_reply_even_with_active_state() -> None:
    state = _state(checkin_date="2026-05-12", checkout_date="2026-05-13", adult_count=4)
    result = _composer().compose(message=_message(), decision=_decision(off=True), state=state)
    assert result.text is None  # off-mode decision carries no customer reply
    assert result.completed_state_id is None


def test_urgent_returns_per_message_reply_even_with_complete_state() -> None:
    state = _state(checkin_date="2026-05-12", checkout_date="2026-05-13", adult_count=4)
    result = _composer().compose(message=_message(), decision=_decision(urgent=True), state=state)
    assert result.text is None
    assert result.completed_state_id is None


def test_incomplete_state_prompts_for_missing_slot() -> None:
    state = _state(checkin_date="2026-05-12", checkout_date="2026-05-13")  # guests missing
    result = _composer().compose(message=_message(), decision=_decision(), state=state)
    assert result.text == SINGLE_MISSING_GUEST_COUNT_MESSAGE
    assert result.completed_state_id is None


def test_complete_state_quotes_and_flags_completion() -> None:
    state = _state(
        id=42, checkin_date="2026-05-12", checkout_date="2026-05-13", adult_count=4
    )
    result = _composer().compose(message=_message(), decision=_decision(), state=state)

    kwargs = dict(
        checkin_date=date(2026, 5, 12), checkout_date=date(2026, 5, 13),
        adult_count=4, child_count=0, infant_count=0, pet_count=0,
    )
    expected = render_quote_message(
        pricing=calculate_price(**kwargs, tenant_pricing=_PRICING, tenant_special_dates={}),
        **kwargs,
    )
    assert result.text == expected
    assert result.completed_state_id == 42


def test_complete_but_over_capacity_returns_capacity_reply() -> None:
    state = _state(
        id=9, checkin_date="2026-05-12", checkout_date="2026-05-13", adult_count=17
    )
    result = _composer().compose(message=_message(), decision=_decision(), state=state)
    assert result.text == render_over_capacity_message()
    assert result.completed_state_id == 9


# ============================================================
# STAGE D: whitelist FAQ answering
# ============================================================


def test_faq_breakfast_tier1_answers_from_config_no_push() -> None:
    result = _composer().compose(
        message=_message("有早餐嗎"), decision=_faq_decision(), state=None
    )
    assert result.text == render_faq_breakfast(breakfast_provided=False)
    assert result.owner_push_text is None  # tier-1 self-contained
    assert result.push_failed_text is None
    assert result.completed_state_id is None


def test_faq_checkout_tier1_answers_from_config() -> None:
    result = _composer().compose(
        message=_message("幾點退房"), decision=_faq_decision(), state=None
    )
    assert result.text == render_faq_checkout(check_in_after="15:00", checkout_before="11:00")
    assert result.owner_push_text is None


def test_faq_pets_tier1_answers_from_pricing_config() -> None:
    result = _composer().compose(
        message=_message("可以帶寵物嗎"), decision=_faq_decision(), state=None
    )
    assert result.text == render_faq_pets(
        allowed_with_notice=True, small_dogs_only=True, fee_twd_per_pet=500
    )
    assert result.owner_push_text is None


def test_faq_wifi_tier2_confirm_and_defer_sets_push_and_both_variants() -> None:
    result = _composer().compose(
        message=_message("有wifi嗎"), decision=_faq_decision(), state=None
    )
    assert result.text.startswith(FAQ_WIFI_LEAD)
    assert "已通知服務人員" in result.text  # the notified (push-success) wording
    assert result.push_failed_text.startswith(FAQ_WIFI_LEAD)
    assert "已通知服務人員" not in result.push_failed_text  # softer wording
    assert result.owner_push_text is not None  # a REAL push is requested
    assert result.completed_state_id is None  # FAQ never completes a state


def test_faq_parking_tier2_confirm_and_defer() -> None:
    result = _composer().compose(
        message=_message("有停車位嗎"), decision=_faq_decision(), state=None
    )
    assert result.text.startswith(FAQ_PARKING_LEAD)
    assert result.owner_push_text is not None


def test_non_whitelist_faq_falls_back_with_push() -> None:
    result = _composer().compose(
        message=_message("附近有什麼好玩的嗎"), decision=_faq_decision(), state=None
    )
    # non-whitelist faq -> generic fallback lead + a real owner push.
    assert "已收到您的訊息" in result.text
    assert result.owner_push_text is not None
    assert result.push_failed_text is not None


def test_faq_owner_push_is_friendly_and_never_leaks_raw_userid() -> None:
    # KEY regression guard: _message uses platform_user_id="Uguest" with no
    # display name; the faq owner push must use the friendly no-name format and
    # NEVER print the raw userId.
    result = _composer().compose(
        message=_message("附近有什麼好玩的嗎"), decision=_faq_decision(), state=None
    )

    assert "📩 有客人訊息待回覆" in result.owner_push_text
    assert "客人問:附近有什麼好玩的嗎" in result.owner_push_text
    assert "Uguest" not in result.owner_push_text
    assert "客人:" not in result.owner_push_text  # no name -> no customer line
    # Truthfulness: the confirm-and-defer reply DID go out to the customer, so
    # this push truthfully claims "系統已回覆…" (NOT the non-asserting close).
    assert "系統已回覆客人會請專人對接" in result.owner_push_text
    assert "尚未回覆客人" not in result.owner_push_text


def test_faq_during_active_quote_does_not_requote_or_complete_state() -> None:
    # A COMPLETE active state would normally quote+complete; a mid-quote FAQ
    # question must instead get the FAQ answer and leave the state untouched.
    state = _state(
        id=99, checkin_date="2026-05-12", checkout_date="2026-05-13", adult_count=4
    )
    result = _composer().compose(
        message=_message("有早餐嗎"), decision=_faq_decision(), state=state
    )
    assert result.text == render_faq_breakfast(breakfast_provided=False)
    assert result.completed_state_id is None  # the open quote state survives


def test_faq_silent_in_off_mode() -> None:
    result = _composer().compose(
        message=_message("有早餐嗎"), decision=_decision(off=True), state=None
    )
    assert result.text is None  # off mode stays receive-only, even for FAQ
    assert result.owner_push_text is None


def test_faq_does_not_override_urgent() -> None:
    result = _composer().compose(
        message=_message("有wifi嗎"), decision=_decision(urgent=True), state=None
    )
    assert result.text is None
    assert result.owner_push_text is None


# ============================================================
# M1: NON_PRICEABLE override — FAQ topic wins over price intent
# ============================================================


def test_non_priceable_breakfast_overrides_price_intent() -> None:
    """'早餐多少錢嗎' classifies as price intent, but breakfast ∈ NON_PRICEABLE
    → composer overrides and answers the breakfast FAQ instead of quoting."""
    result = _composer().compose(
        message=_message("早餐多少錢嗎"),
        decision=_price_intent_decision(),
        state=None,
    )
    assert result.text == render_faq_breakfast(breakfast_provided=False)
    assert result.owner_push_text is None
    assert result.completed_state_id is None


def test_non_priceable_parking_overrides_price_intent() -> None:
    """'停車要多少錢嗎' contains a price keyword + parking topic;
    parking ∈ NON_PRICEABLE → tier-2 confirm-and-defer, NOT a quote."""
    result = _composer().compose(
        message=_message("停車要多少錢嗎"),
        decision=_price_intent_decision(),
        state=None,
    )
    assert result.text.startswith(FAQ_PARKING_LEAD)
    assert result.owner_push_text is not None
    assert result.completed_state_id is None


def test_non_priceable_pets_overrides_price_intent() -> None:
    """'帶寵物多少錢嗎' hits pets ∈ NON_PRICEABLE → pets FAQ answer."""
    result = _composer().compose(
        message=_message("帶寵物多少錢嗎"),
        decision=_price_intent_decision(),
        state=None,
    )
    assert result.text == render_faq_pets(
        allowed_with_notice=True, small_dogs_only=True, fee_twd_per_pet=500
    )
    assert result.owner_push_text is None
    assert result.completed_state_id is None


def test_non_priceable_wifi_overrides_price_intent() -> None:
    """'請問有wifi費用嗎' hits wifi ∈ NON_PRICEABLE → tier-2 confirm-and-defer."""
    result = _composer().compose(
        message=_message("請問有wifi費用嗎"),
        decision=_price_intent_decision(),
        state=None,
    )
    assert result.text.startswith(FAQ_WIFI_LEAD)
    assert result.owner_push_text is not None
    assert result.completed_state_id is None


def test_regression_pure_price_inquiry_no_faq_topic_not_hijacked() -> None:
    """'四房全開多少錢' has no FAQ topic match → faq_match is None →
    NON_PRICEABLE override skips → falls through to state-driven path."""
    result = _composer().compose(
        message=_message("四房全開多少錢"),
        decision=_price_intent_decision(),
        state=None,
    )
    # No active state → per-message price reply returned unchanged.
    assert result.text == "PER_MESSAGE_PRICE_REPLY"
    assert result.completed_state_id is None


def test_regression_non_priceable_does_not_fire_in_off_mode() -> None:
    """was_system_off is still highest priority — overrides even NON_PRICEABLE."""
    result = _composer().compose(
        message=_message("早餐多少錢嗎"),
        decision=_decision(off=True),
        state=None,
    )
    assert result.text is None
    assert result.owner_push_text is None


def test_regression_non_priceable_does_not_fire_when_urgent() -> None:
    """was_urgent is still highest priority."""
    result = _composer().compose(
        message=_message("早餐多少錢嗎"),
        decision=_decision(urgent=True),
        state=None,
    )
    assert result.text is None
    assert result.owner_push_text is None


def test_regression_faq_fallback_no_topic_still_works() -> None:
    """'附近有什麼好玩的嗎' has no FAQ topic → faq_match is None → existing
    _is_faq fallback path handles it unchanged."""
    result = _composer().compose(
        message=_message("附近有什麼好玩的嗎"),
        decision=_faq_decision(),
        state=None,
    )
    assert "已收到您的訊息" in result.text
    assert result.owner_push_text is not None
