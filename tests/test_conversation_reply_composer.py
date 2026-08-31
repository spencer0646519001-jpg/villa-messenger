"""
Unit tests for ConversationReplyComposer (STAGE C reply selection), exercised in
isolation with fake pricing loaders and hand-built state rows / decisions.

Covers the four branches of compose():
  - no active state / off mode / urgent -> the per-message reply (fallback)
  - active + incomplete -> missing-slot prompt (from the accumulated state)
  - active + complete -> quote from accumulated slots + completed_state_id
  - active + manual-review gate -> owner handoff + completed_state_id
"""

from datetime import date, datetime, timedelta, timezone

import pytest

from app.domain.availability_models import AvailabilityResult, BlockedNight
from app.domain.inquiry_decision import InquiryDecision
from app.domain.pricing_policy import calculate_price
from app.domain.reply_templates import (
    render_faq_amenities,
    render_faq_bbq,
    render_faq_breakfast,
    render_faq_checkout,
    render_faq_location,
    render_faq_parking,
    render_faq_pets,
    render_faq_room_type,
    render_faq_wifi,
    render_faq_whole_house,
    render_manual_review_message,
    render_quote_message,
    render_reconfirm_stale_context_message,
)
from app.domain.reply_text import (
    FULL_HOUSE_MESSAGE,
    MISSING_ROOM_COUNT_MESSAGE,
    OWNER_PUSH_AVAILABILITY_UNVERIFIED_PREFIX,
    OWNER_PUSH_FULL_HOUSE_GUEST_COUNT_PREFIX,
    OWNER_PUSH_FULL_HOUSE_GUEST_COUNT_UNKNOWN,
    OWNER_PUSH_FULL_HOUSE_PREFIX,
    ROOM_CAPACITY_SUGGESTION_TEMPLATE,
    SINGLE_MISSING_CHECKOUT_MESSAGE,
    SINGLE_MISSING_GUEST_COUNT_MESSAGE,
)
from app.schemas import InboundMessage
from app.services.availability_service import AvailabilityCheckOutcome
from app.services.conversation_reply_composer import (
    ComposedReply,
    ConversationReplyComposer,
)

_AMENITIES = {"items": ["不限時 KTV", "Switch、電動麻將"]}
_ROOM_POLICY_FAKE = {
    "description": "3層樓電梯別墅,共4間房。",
    "standard_capacity": 12,
    "max_capacity": 16,
    "room_opening_rules": [
        {"max_people": 8, "rooms_opened": 2},
        {"max_people": 10, "rooms_opened": 3},
        {"max_people": 12, "rooms_opened": 4},
        {"min_people": 13, "max_people": 16, "rooms_opened": 4, "extra_beds": True},
    ],
}
_LOCATION_FAKE = {"address": "宜蘭縣員山鄉枕山十二路123號"}

_PRICING = {
    "base_prices_per_night": {
        "8_people": {"weekday": 9000, "saturday": 15000, "summer_weekday": 12000,
                     "summer_saturday_or_holiday": 15000, "spring_festival": 25000},
        "10_people": {"weekday": 12000, "saturday": 18000, "summer_weekday": 15000,
                      "summer_saturday_or_holiday": 18000, "spring_festival": 28000},
        "12_people": {"weekday": 15000, "saturday": 21000, "summer_weekday": 18000,
                      "summer_saturday_or_holiday": 21000, "spring_festival": 31000},
    },
    "pets": {
        "allowed_with_notice": True,
        "small_dogs_only_for_now": True,
        "fee_twd_per_pet_per_stay": 500,
    },
    "bbq": {
        "cleaning_fee_twd": 1000,
    },
    "deposits": {
        "booking_deposit_percent_of_total_room_price": 30,
        "equipment_security_deposit_on_arrival_twd": 3000,
    },
}

_STAY_POLICY = {
    "breakfast_provided": False,
    "check_in_after": "15:00",
    "checkout_before": "11:00",
    "wifi_provided": True,
    "wifi_free": True,
    "parking_available": True,
    "parking_free": True,
}


class _FakeAvailabilityService:
    def __init__(self, *, outcome: AvailabilityCheckOutcome) -> None:
        self._outcome = outcome
        self.enabled = True
        self.calls: list[tuple[date, date]] = []

    def check(self, *, checkin_date: date, checkout_date: date) -> AvailabilityCheckOutcome:
        self.calls.append((checkin_date, checkout_date))
        return self._outcome


def _composer(availability_service=None, now_provider=None) -> ConversationReplyComposer:
    return ConversationReplyComposer(
        tenant_pricing_loader=lambda tid: _PRICING,
        tenant_special_dates_loader=lambda tid: {},
        tenant_stay_policy_loader=lambda tid: _STAY_POLICY,
        tenant_amenities_loader=lambda tid: _AMENITIES,
        tenant_room_policy_loader=lambda tid: _ROOM_POLICY_FAKE,
        tenant_location_loader=lambda tid: _LOCATION_FAKE,
        availability_service=availability_service,
        now_provider=now_provider,
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


def _non_inquiry_decision(log_payload: dict | None = None) -> InquiryDecision:
    """Simulates InquiryService result for a message that triggers no FAQ signal
    term (no 嗎 / no ?) — inquiry_intent='unknown', no customer reply.
    gate2 (_is_faq) will NOT fire for this decision; gate3 is the only path
    that can route it to a tier-1 FAQ answer."""
    return InquiryDecision(
        action_type="push_to_owner_only",
        owner_push_text="(owner push)",
        log_payload=log_payload or {"inquiry_intent": "unknown"},
        parsed_as_inquiry=False,
    )


def _decision(
    *,
    off: bool = False,
    urgent: bool = False,
    parsed_checkin: str | None = None,
    parsed_checkout: str | None = None,
) -> InquiryDecision:
    payload = {"a": 1}
    if parsed_checkin is not None:
        payload["parsed_checkin"] = parsed_checkin
    if parsed_checkout is not None:
        payload["parsed_checkout"] = parsed_checkout
    if urgent:
        return InquiryDecision(
            action_type="push_owner_urgent", owner_push_text="!", was_urgent=True,
            log_payload=payload,
        )
    if off:
        return InquiryDecision(
            action_type="do_nothing", was_system_off=True, log_payload=payload,
        )
    return InquiryDecision(
        action_type="reply_to_customer_only", customer_reply_text="PER_MESSAGE",
        log_payload=payload,
    )


def _state(**overrides) -> dict:
    base = {
        "id": 7, "status": "in_progress",
        "checkin_date": None, "checkout_date": None,
        "adult_count": None, "child_count": None, "infant_count": None,
        "room_count": None,
        "pet_count": None, "has_pet": 0,
    }
    base.update(overrides)
    return base


def _availability_blocked() -> AvailabilityCheckOutcome:
    return AvailabilityCheckOutcome(
        status="blocked",
        result=AvailabilityResult(
            has_any_blocked_nights=True,
            blocked_nights=[
                BlockedNight(
                    night_date=date(2026, 5, 12),
                    blocking_event_summary="枕123",
                    matched_keyword="枕",
                )
            ],
        ),
    )


def _availability_available() -> AvailabilityCheckOutcome:
    return AvailabilityCheckOutcome(
        status="available",
        result=AvailabilityResult(has_any_blocked_nights=False, blocked_nights=[]),
    )


def _availability_error() -> AvailabilityCheckOutcome:
    return AvailabilityCheckOutcome(status="error", error_reason="network down")


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


def test_urgent_preserves_owner_push_text_for_owner_delivery() -> None:
    decision = _decision(urgent=True)
    result = _composer().compose(message=_message(), decision=decision, state=None)
    assert result.text is None
    assert result.owner_push_text == decision.owner_push_text


def test_no_active_state_true_non_inquiry_preserves_owner_push_for_delivery() -> None:
    decision = _non_inquiry_decision()
    result = _composer().compose(message=_message(), decision=decision, state=None)
    assert result.text is None
    assert result.owner_push_text == decision.owner_push_text


def test_no_active_state_customer_reply_path_does_not_forward_owner_push() -> None:
    decision = InquiryDecision(
        action_type="reply_and_push",
        customer_reply_text="CUSTOMER_REPLY",
        owner_push_text="OWNER_PUSH",
        log_payload={"inquiry_intent": "price"},
        parsed_as_inquiry=True,
    )
    result = _composer().compose(message=_message(), decision=decision, state=None)
    assert result.text == "CUSTOMER_REPLY"
    assert result.owner_push_text is None


def test_incomplete_state_prompts_for_missing_slot() -> None:
    state = _state(checkin_date="2026-05-12", checkout_date="2026-05-13")  # guests missing
    result = _composer().compose(message=_message(), decision=_decision(), state=state)
    assert result.text == SINGLE_MISSING_GUEST_COUNT_MESSAGE
    assert result.completed_state_id is None


def test_early_date_range_blocked_stops_missing_prompt_and_notifies_owner() -> None:
    service = _FakeAvailabilityService(outcome=_availability_blocked())
    state = _state(
        id=42,
        checkin_date="2026-05-12",
        checkout_date="2026-05-13",
    )

    result = _composer(availability_service=service).compose(
        message=_message(),
        decision=_decision(
            parsed_checkin="2026-05-12",
            parsed_checkout="2026-05-13",
        ),
        state=state,
    )

    assert result.text == FULL_HOUSE_MESSAGE
    assert result.owner_push_text is not None
    assert OWNER_PUSH_FULL_HOUSE_PREFIX in result.owner_push_text
    assert OWNER_PUSH_FULL_HOUSE_GUEST_COUNT_UNKNOWN in result.owner_push_text
    assert "Uguest" not in result.owner_push_text  # raw userId never printed
    assert result.completed_state_id == 42
    assert service.calls == [(date(2026, 5, 12), date(2026, 5, 13))]


def test_early_available_continues_to_missing_guest_prompt() -> None:
    service = _FakeAvailabilityService(outcome=_availability_available())
    state = _state(checkin_date="2026-05-12", checkout_date="2026-05-13")

    result = _composer(availability_service=service).compose(
        message=_message(),
        decision=_decision(
            parsed_checkin="2026-05-12",
            parsed_checkout="2026-05-13",
        ),
        state=state,
    )

    assert result.text == SINGLE_MISSING_GUEST_COUNT_MESSAGE
    assert result.owner_push_text is None
    assert result.completed_state_id is None
    assert service.calls == [(date(2026, 5, 12), date(2026, 5, 13))]


def test_early_error_degrades_to_missing_guest_prompt() -> None:
    service = _FakeAvailabilityService(outcome=_availability_error())
    state = _state(checkin_date="2026-05-12", checkout_date="2026-05-13")

    result = _composer(availability_service=service).compose(
        message=_message(),
        decision=_decision(
            parsed_checkin="2026-05-12",
            parsed_checkout="2026-05-13",
        ),
        state=state,
    )

    assert result.text == SINGLE_MISSING_GUEST_COUNT_MESSAGE
    assert result.owner_push_text is None
    assert result.completed_state_id is None
    assert service.calls == [(date(2026, 5, 12), date(2026, 5, 13))]


def test_early_gate_waits_until_date_range_is_complete() -> None:
    service = _FakeAvailabilityService(outcome=_availability_blocked())
    state = _state(checkin_date="2026-05-12", checkout_date=None, adult_count=4)

    result = _composer(availability_service=service).compose(
        message=_message(),
        decision=_decision(parsed_checkin="2026-05-12"),
        state=state,
    )

    assert result.text == SINGLE_MISSING_CHECKOUT_MESSAGE
    assert result.owner_push_text is None
    assert result.completed_state_id is None
    assert service.calls == []


def test_multiturn_checkout_completion_triggers_early_block() -> None:
    service = _FakeAvailabilityService(outcome=_availability_blocked())
    state = _state(
        id=43,
        checkin_date="2026-05-12",
        checkout_date="2026-05-13",
    )

    result = _composer(availability_service=service).compose(
        message=_message(),
        decision=_decision(parsed_checkout="2026-05-13"),
        state=state,
    )

    assert result.text == FULL_HOUSE_MESSAGE
    assert result.owner_push_text is not None
    assert OWNER_PUSH_FULL_HOUSE_GUEST_COUNT_UNKNOWN in result.owner_push_text
    assert result.completed_state_id == 43
    assert service.calls == [(date(2026, 5, 12), date(2026, 5, 13))]


def test_complete_state_without_room_count_asks_room_count() -> None:
    state = _state(
        checkin_date="2026-05-12",
        checkout_date="2026-05-13",
        adult_count=4,
    )

    result = _composer().compose(message=_message(), decision=_decision(), state=state)

    assert result.text == MISSING_ROOM_COUNT_MESSAGE
    assert result.completed_state_id is None


def test_complete_state_with_too_few_rooms_suggests_more_rooms() -> None:
    state = _state(
        checkin_date="2026-05-12",
        checkout_date="2026-05-13",
        adult_count=10,
        room_count=2,
    )

    result = _composer().compose(message=_message(), decision=_decision(), state=state)

    assert result.text == ROOM_CAPACITY_SUGGESTION_TEMPLATE.format(
        guest_count=10,
        room_count=2,
        suggested_room_count=3,
    )
    assert result.completed_state_id is None


def test_complete_state_quotes_and_flags_completion() -> None:
    state = _state(
        id=42,
        checkin_date="2026-05-12",
        checkout_date="2026-05-13",
        adult_count=4,
        room_count=2,
    )
    result = _composer().compose(message=_message(), decision=_decision(), state=state)

    kwargs = dict(
        checkin_date=date(2026, 5, 12), checkout_date=date(2026, 5, 13),
        adult_count=4, child_count=0, infant_count=0, pet_count=0, room_count=2,
    )
    expected = render_quote_message(
        pricing=calculate_price(
            **kwargs,
            tenant_pricing=_PRICING,
            tenant_special_dates={},
            room_policy=_ROOM_POLICY_FAKE,
        ),
        **kwargs,
    )
    assert result.text == expected
    assert result.completed_state_id == 42


def test_completed_per_message_quote_reuses_decision_without_availability_check() -> None:
    service = _FakeAvailabilityService(outcome=_availability_blocked())
    state = _state(
        id=42,
        checkin_date="2026-05-12",
        checkout_date="2026-05-13",
        adult_count=4,
        room_count=2,
    )
    decision = InquiryDecision(
        action_type="reply_to_customer_only",
        customer_reply_text="PER_MESSAGE_QUOTE",
        log_payload={"inquiry_intent": "price"},
        parsed_as_inquiry=True,
        could_quote=True,
        completes_conversation_state=True,
    )

    result = _composer(availability_service=service).compose(
        message=_message(), decision=decision, state=state
    )

    assert result.text == "PER_MESSAGE_QUOTE"
    assert result.completed_state_id == 42
    assert service.calls == []


def test_complete_state_blocked_availability_returns_full_house_without_quote() -> None:
    service = _FakeAvailabilityService(outcome=_availability_blocked())
    state = _state(
        id=42,
        checkin_date="2026-05-12",
        checkout_date="2026-05-13",
        adult_count=4,
        room_count=2,
    )

    result = _composer(availability_service=service).compose(
        message=_message(), decision=_decision(), state=state
    )

    assert result.text == FULL_HOUSE_MESSAGE
    assert result.owner_push_text is not None
    assert OWNER_PUSH_FULL_HOUSE_PREFIX in result.owner_push_text
    assert f"{OWNER_PUSH_FULL_HOUSE_GUEST_COUNT_PREFIX}4" in result.owner_push_text
    assert "Uguest" not in result.owner_push_text  # raw userId never printed
    assert result.completed_state_id == 42
    assert service.calls == [(date(2026, 5, 12), date(2026, 5, 13))]


def test_complete_state_availability_error_quotes_and_notifies_owner() -> None:
    service = _FakeAvailabilityService(outcome=_availability_error())
    state = _state(
        id=42,
        checkin_date="2026-05-12",
        checkout_date="2026-05-13",
        adult_count=4,
        room_count=2,
    )

    result = _composer(availability_service=service).compose(
        message=_message(), decision=_decision(), state=state
    )

    assert result.text is not None
    assert "NT$9,000" in result.text
    assert result.owner_push_text is not None
    assert OWNER_PUSH_AVAILABILITY_UNVERIFIED_PREFIX in result.owner_push_text
    assert result.completed_state_id == 42


def test_complete_but_over_capacity_returns_manual_review() -> None:
    state = _state(
        id=9,
        checkin_date="2026-05-12",
        checkout_date="2026-05-13",
        adult_count=17,
        room_count=4,
    )
    result = _composer().compose(message=_message(), decision=_decision(), state=state)
    assert result.text == render_manual_review_message()
    assert result.owner_push_text is not None
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


def test_faq_wifi_tier1_answers_from_config() -> None:
    result = _composer().compose(
        message=_message("有wifi嗎"), decision=_faq_decision(), state=None
    )
    assert result.text == render_faq_wifi(provided=True, free=True)
    assert "免費" in result.text
    assert result.owner_push_text is None
    assert result.push_failed_text is None
    assert result.completed_state_id is None


def test_faq_parking_tier1_answers_from_config() -> None:
    result = _composer().compose(
        message=_message("有停車位嗎"), decision=_faq_decision(), state=None
    )
    assert result.text == render_faq_parking(available=True, free=True)
    assert "免費" in result.text
    assert result.owner_push_text is None


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
    assert result.owner_push_text is not None


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
    parking ∈ NON_PRICEABLE → tier-1 direct answer, NOT a quote."""
    result = _composer().compose(
        message=_message("停車要多少錢嗎"),
        decision=_price_intent_decision(),
        state=None,
    )
    assert result.text == render_faq_parking(available=True, free=True)
    assert "免費" in result.text
    assert result.owner_push_text is None
    assert result.push_failed_text is None
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
    """'請問有wifi費用嗎' hits wifi ∈ NON_PRICEABLE → tier-1 direct answer."""
    result = _composer().compose(
        message=_message("請問有wifi費用嗎"),
        decision=_price_intent_decision(),
        state=None,
    )
    assert result.text == render_faq_wifi(provided=True, free=True)
    assert "免費" in result.text
    assert result.owner_push_text is None
    assert result.push_failed_text is None
    assert result.completed_state_id is None


def test_decision_f_bbq_price_intent_is_policy_faq_not_quote() -> None:
    """Decision F boundary: BBQ has a fixed policy fee, not a room quote item.
    'BBQ 多少錢' is price intent but bbq ∈ NON_PRICEABLE → direct policy FAQ."""
    result = _composer().compose(
        message=_message("BBQ 多少錢"),
        decision=_price_intent_decision(),
        state=None,
    )
    assert result.text == render_faq_bbq(cleaning_fee_twd=1000)
    assert result.owner_push_text is None
    assert result.push_failed_text is None
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
    assert result.owner_push_text == "!"


# ============================================================
# M2.0: _tier1_answer safety net — unknown topic must raise
# ============================================================


def test_tier1_answer_raises_for_unknown_topic() -> None:
    """_tier1_answer with a topic not in the handled set must raise ValueError,
    not silently fall through to the pets branch.  FaqTopic is a Literal so the
    type-checker would catch this at analysis time, but Literal is not enforced
    at runtime — we verify the explicit raise guard here."""
    with pytest.raises(ValueError, match="unhandled tier-1 FAQ topic"):
        _composer()._tier1_answer(1, "facilities")


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


# ============================================================
# M2.2a: whole_house tier-1 FAQ
# ============================================================


def test_faq_whole_house_tier1_answers_with_no_push() -> None:
    """'是包棟嗎' hits whole_house tier-1 → fixed sentence, no owner push."""
    result = _composer().compose(
        message=_message("是包棟嗎"), decision=_faq_decision(), state=None
    )
    assert result.text == render_faq_whole_house()
    assert "枕123" in result.text
    assert "包棟" in result.text
    assert result.owner_push_text is None
    assert result.push_failed_text is None
    assert result.completed_state_id is None


def test_faq_whole_house_keyword_zheng_dong() -> None:
    """'整棟租嗎' 也命中 whole_house tier-1。"""
    result = _composer().compose(
        message=_message("整棟租嗎"), decision=_faq_decision(), state=None
    )
    assert result.text == render_faq_whole_house()
    assert result.owner_push_text is None


def test_regression_whole_house_price_inquiry_not_hijacked() -> None:
    """⚠️ 決策F 回歸:「包棟多少錢」是 price intent,whole_house ∉ NON_PRICEABLE
    → gate3 加 price 條件後不放行 → 走報價路徑,沿用 per-message price reply。"""
    result = _composer().compose(
        message=_message("包棟多少錢"),
        decision=_price_intent_decision(),
        state=None,
    )
    assert result.text == "PER_MESSAGE_PRICE_REPLY"
    assert result.owner_push_text is None
    assert result.completed_state_id is None


# ============================================================
# M2.2b: 設備 / 房型 / 位置 三主題 tier-1
# ============================================================


def test_faq_amenities_tier1_answers_with_bullet_list() -> None:
    result = _composer().compose(
        message=_message("有什麼設備"), decision=_faq_decision(), state=None
    )
    assert result.text == render_faq_amenities(items=_AMENITIES["items"])
    assert "枕123 提供的設備:" in result.text
    assert "・不限時 KTV" in result.text
    assert "・Switch、電動麻將" in result.text
    assert result.owner_push_text is None
    assert result.push_failed_text is None
    assert result.completed_state_id is None


def test_faq_amenities_keyword_she_shi() -> None:
    result = _composer().compose(
        message=_message("有哪些設施"), decision=_faq_decision(), state=None
    )
    assert "枕123 提供的設備:" in result.text
    assert result.owner_push_text is None


def test_faq_room_type_tier1_answers_with_description() -> None:
    result = _composer().compose(
        message=_message("有什麼房型"), decision=_faq_decision(), state=None
    )
    assert result.text == render_faq_room_type(description=_ROOM_POLICY_FAKE["description"])
    assert "3層樓電梯別墅" in result.text
    assert "4間房" in result.text
    assert result.owner_push_text is None
    assert result.push_failed_text is None
    assert result.completed_state_id is None


def test_faq_room_type_keyword_lou_ceng() -> None:
    result = _composer().compose(
        message=_message("有幾個樓層"), decision=_faq_decision(), state=None
    )
    assert "3層樓電梯別墅" in result.text
    assert result.owner_push_text is None


def test_faq_room_type_keyword_ji_jian_fang() -> None:
    result = _composer().compose(
        message=_message("有幾間房"), decision=_faq_decision(), state=None
    )
    assert "4間房" in result.text
    assert result.owner_push_text is None


def test_faq_room_type_keyword_ji_ren_fang() -> None:
    result = _composer().compose(
        message=_message("幾人房"), decision=_faq_decision(), state=None
    )
    assert "3層樓電梯別墅" in result.text
    assert result.owner_push_text is None


def test_faq_location_tier1_answers_with_full_address() -> None:
    result = _composer().compose(
        message=_message("地址是什麼"), decision=_faq_decision(), state=None
    )
    assert result.text == render_faq_location(address=_LOCATION_FAKE["address"])
    assert "宜蘭縣員山鄉枕山十二路123號" in result.text
    assert "枕123 位於" in result.text
    assert result.owner_push_text is None
    assert result.push_failed_text is None
    assert result.completed_state_id is None


def test_faq_location_keyword_wei_zhi() -> None:
    result = _composer().compose(
        message=_message("民宿位置"), decision=_faq_decision(), state=None
    )
    assert "宜蘭縣員山鄉枕山十二路123號" in result.text
    assert result.owner_push_text is None


def test_faq_location_keyword_zen_me_qu() -> None:
    result = _composer().compose(
        message=_message("怎麼去"), decision=_faq_decision(), state=None
    )
    assert "宜蘭縣員山鄉枕山十二路123號" in result.text
    assert result.owner_push_text is None


def test_faq_location_keyword_di_dian() -> None:
    result = _composer().compose(
        message=_message("地點在哪"), decision=_faq_decision(), state=None
    )
    assert "宜蘭縣員山鄉枕山十二路123號" in result.text
    assert result.owner_push_text is None


def test_faq_location_keyword_na_li() -> None:
    # Real LINE E2E regression: "請問你們家在哪裡" (contains neither 地址/位置/
    # 怎麼去/地點) hit no location keyword at all -- TYPE_6 correctly
    # upgraded intent to "faq" but with no topic match the reply fell back
    # to the generic "已通知服務人員" defer instead of the actual address,
    # and needlessly triggered an owner push for a fully answerable
    # question. This was never a TYPE_6 routing bug -- _is_faq already
    # correctly reaches this composer for any faq-classified message; the
    # gap was purely this keyword list. Matched via "你們家在哪" (a compound
    # phrase, not the bare interrogative "在哪"/"哪裡" -- see the test below
    # for why bare interrogatives were rejected).
    result = _composer().compose(
        message=_message("請問你們家在哪裡"), decision=_faq_decision(), state=None
    )
    assert "宜蘭縣員山鄉枕山十二路123號" in result.text
    assert result.owner_push_text is None


def test_faq_location_does_not_match_unrelated_where_questions() -> None:
    # Codex review of the first version of this fix: bare "在哪"/"哪裡" are
    # unrestricted interrogatives, not specific to the property's own
    # location -- "附近哪裡有便利商店" is asking about a NEARBY convenience
    # store, and answering with the homestay's own street address (while
    # also suppressing the owner push that would otherwise defer this
    # unsupported question to a human) is actively wrong, not just unhelpful.
    result = _composer().compose(
        message=_message("附近哪裡有便利商店？"), decision=_faq_decision(), state=None
    )
    assert "宜蘭縣員山鄉枕山十二路123號" not in result.text


def test_faq_amenities_empty_items_returns_safe_fallback() -> None:
    composer = ConversationReplyComposer(
        tenant_pricing_loader=lambda tid: _PRICING,
        tenant_special_dates_loader=lambda tid: {},
        tenant_stay_policy_loader=lambda tid: _STAY_POLICY,
        tenant_amenities_loader=lambda tid: {"items": []},
        tenant_room_policy_loader=lambda tid: _ROOM_POLICY_FAKE,
        tenant_location_loader=lambda tid: _LOCATION_FAKE,
    )
    result = composer.compose(
        message=_message("有什麼設備"), decision=_faq_decision(), state=None
    )
    assert result.text is not None
    assert "None" not in result.text
    assert result.owner_push_text is None


def test_faq_room_type_none_description_returns_safe_fallback() -> None:
    composer = ConversationReplyComposer(
        tenant_pricing_loader=lambda tid: _PRICING,
        tenant_special_dates_loader=lambda tid: {},
        tenant_stay_policy_loader=lambda tid: _STAY_POLICY,
        tenant_amenities_loader=lambda tid: _AMENITIES,
        tenant_room_policy_loader=lambda tid: {},
        tenant_location_loader=lambda tid: _LOCATION_FAKE,
    )
    result = composer.compose(
        message=_message("房型"), decision=_faq_decision(), state=None
    )
    assert result.text is not None
    assert "None" not in result.text
    assert result.owner_push_text is None


def test_faq_location_none_address_returns_safe_fallback() -> None:
    composer = ConversationReplyComposer(
        tenant_pricing_loader=lambda tid: _PRICING,
        tenant_special_dates_loader=lambda tid: {},
        tenant_stay_policy_loader=lambda tid: _STAY_POLICY,
        tenant_amenities_loader=lambda tid: _AMENITIES,
        tenant_room_policy_loader=lambda tid: _ROOM_POLICY_FAKE,
        tenant_location_loader=lambda tid: {},
    )
    result = composer.compose(
        message=_message("地址"), decision=_faq_decision(), state=None
    )
    assert result.text is not None
    assert "None" not in result.text
    assert result.owner_push_text is None


def test_regression_raise_guard_still_fires_for_unknown_topic_after_new_branches() -> None:
    with pytest.raises(ValueError, match="unhandled tier-1 FAQ topic"):
        _composer()._tier1_answer(1, "totally_unknown_topic")


def test_regression_new_topics_not_in_non_priceable_price_intent_not_hijacked() -> None:
    """⚠️ 決策F 回歸:設備/房型/位置 ∉ NON_PRICEABLE,且 gate3 排除 price intent
    → 問價句不被劫持進 FAQ,沿用 per-message price reply。"""
    for text in ("設備多少錢", "房型多少錢", "地址多少錢"):
        result = _composer().compose(
            message=_message(text),
            decision=_price_intent_decision(),
            state=None,
        )
        assert result.text == "PER_MESSAGE_PRICE_REPLY", f"failed for: {text!r}"
        assert result.owner_push_text is None


# ============================================================
# M2.x: 無問號 tier-1 FAQ 直答 (gate3) + checkout regression
# ============================================================


def test_gate3_amenities_no_question_mark() -> None:
    """「設備有哪些」has no 嗎/? → gate2 (_is_faq) would not fire, but gate3
    (tier-1, topic≠checkout) fires → amenities answer, owner_push_text is None."""
    result = _composer().compose(
        message=_message("設備有哪些"),
        decision=_non_inquiry_decision(),
        state=None,
    )
    assert result.text == render_faq_amenities(items=_AMENITIES["items"])
    assert result.owner_push_text is None
    assert result.completed_state_id is None


def test_gate3_room_type_no_question_mark() -> None:
    """「房型樓層說明」has no 嗎/? → gate3 fires → room_type description."""
    result = _composer().compose(
        message=_message("房型樓層說明"),
        decision=_non_inquiry_decision(),
        state=None,
    )
    assert result.text == render_faq_room_type(description=_ROOM_POLICY_FAKE["description"])
    assert result.owner_push_text is None
    assert result.completed_state_id is None


def test_gate3_location_no_question_mark() -> None:
    """「地址」standalone (no 嗎/?) → gate3 fires → location address."""
    result = _composer().compose(
        message=_message("地址"),
        decision=_non_inquiry_decision(),
        state=None,
    )
    assert result.text == render_faq_location(address=_LOCATION_FAKE["address"])
    assert result.owner_push_text is None
    assert result.completed_state_id is None


def test_gate3_whole_house_no_question_mark() -> None:
    """「是包棟」(no 嗎/?) → gate3 fires → whole_house fixed sentence."""
    result = _composer().compose(
        message=_message("是包棟"),
        decision=_non_inquiry_decision(),
        state=None,
    )
    assert result.text == render_faq_whole_house()
    assert result.owner_push_text is None
    assert result.completed_state_id is None


def test_whole_house_availability_intent_is_not_hijacked_by_gate3() -> None:
    decision = InquiryDecision(
        action_type="reply_to_customer_only",
        customer_reply_text=SINGLE_MISSING_CHECKOUT_MESSAGE,
        log_payload={"inquiry_intent": "availability"},
        parsed_as_inquiry=True,
    )
    result = _composer().compose(
        message=_message("8/15可以包棟嗎 9人"),
        decision=decision,
        state=None,
    )

    assert result.text == SINGLE_MISSING_CHECKOUT_MESSAGE


def test_product_topic_prevents_earlier_policy_topic_from_hijacking_quote() -> None:
    decision = InquiryDecision(
        action_type="reply_to_customer_only",
        customer_reply_text=SINGLE_MISSING_CHECKOUT_MESSAGE,
        log_payload={"inquiry_intent": "availability"},
        parsed_as_inquiry=True,
    )
    result = _composer().compose(
        message=_message("8/15可以帶寵物包棟嗎 9人"),
        decision=decision,
        state=None,
    )

    assert result.text == SINGLE_MISSING_CHECKOUT_MESSAGE


def test_bbq_mention_does_not_override_a_resolved_booking_reply() -> None:
    # Real LINE E2E regression: "9/20-9/22入住,8大2小,想烤肉" parsed and
    # persisted correctly (intent=availability, dates, guests, wants_bbq=1
    # all confirmed in the DB), but the customer only received the bare
    # BBQ policy text ("烤肉需事先預約,清潔費1000元") instead of the booking/
    # availability reply. bbq ∈ NON_PRICEABLE, and unlike whole_house
    # (is_booking_equivalent_topic), it had no exemption at all once real,
    # resolved date+guest slots are present -- the booking reply must stay
    # primary and BBQ information may be appended, but never replace it.
    decision = InquiryDecision(
        action_type="reply_to_customer_only",
        customer_reply_text="BOOKING_REPLY",
        log_payload={
            "inquiry_intent": "availability",
            "parsed_checkin": "2026-09-20",
            "parsed_checkout": "2026-09-22",
            "parsed_adult_count": 8,
            "parsed_child_count": 2,
            "parsed_wants_bbq": True,
        },
        parsed_as_inquiry=True,
    )
    result = _composer().compose(
        message=_message("9/20-9/22入住，8大2小，想烤肉"),
        decision=decision,
        state=None,
    )

    assert result.text == "BOOKING_REPLY"
    assert result.text != render_faq_bbq(cleaning_fee_twd=1000)


def test_bbq_faq_still_wins_with_no_resolved_booking_slots() -> None:
    # Must-preserve companion to the test above (Decision F): "BBQ多少錢"
    # classifies as price intent too, but carries NO resolved dates/guests
    # at all -- nothing to quote against, so the fixed BBQ policy answer
    # stays correct. Same assertions as
    # test_decision_f_bbq_price_intent_is_policy_faq_not_quote, re-asserted
    # here to document the boundary the new exemption must not cross.
    result = _composer().compose(
        message=_message("BBQ多少錢"),
        decision=_price_intent_decision(),
        state=None,
    )

    assert result.text == render_faq_bbq(cleaning_fee_twd=1000)


def test_bbq_faq_still_wins_with_guest_count_but_no_dates() -> None:
    # Codex review of the first version of the booking-context exemption:
    # "8人BBQ多少錢" rule-parses adult_count=8 alongside intent="price", with
    # NO dates at all -- a guest count mentioned in a hypothetical/policy
    # question is not evidence of an active booking the way committed dates
    # are. Treating it as sufficient produced a "please give me your dates"
    # booking prompt instead of the fixed BBQ policy answer.
    decision = InquiryDecision(
        action_type="reply_to_customer_only",
        customer_reply_text="SINGLE_MISSING_DATES_PROMPT",
        log_payload={"inquiry_intent": "price", "parsed_adult_count": 8},
        parsed_as_inquiry=True,
    )
    result = _composer().compose(
        message=_message("8人 BBQ 多少錢"), decision=decision, state=None
    )

    assert result.text == render_faq_bbq(cleaning_fee_twd=1000)


_FORM_REPLY_TEXT = (
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


def test_structured_form_reply_not_hijacked_by_pets_faq() -> None:
    """The real production bug: a filled-in LINE OA intake form contains the
    "寵物"/"烤肉" FAQ keywords as answered field labels, not questions. It must
    not be routed to the pets/bbq tier-1 FAQ answer."""
    decision = InquiryDecision(
        action_type="reply_to_customer_only",
        customer_reply_text=SINGLE_MISSING_CHECKOUT_MESSAGE,
        log_payload={"inquiry_intent": "booking_question"},
        parsed_as_inquiry=True,
    )
    result = _composer().compose(
        message=_message(_FORM_REPLY_TEXT), decision=decision, state=None,
    )

    assert result.text != render_faq_pets(
        allowed_with_notice=True, small_dogs_only=True, fee_twd_per_pet=500
    )
    assert result.text != render_faq_bbq(cleaning_fee_twd=1000)
    assert result.text == SINGLE_MISSING_CHECKOUT_MESSAGE


def test_structured_form_reply_preserves_owner_push_when_no_customer_reply() -> None:
    """Same message, but shaped like the push-owner-only decision inquiry_service
    would produce when there's no immediate customer reply -- the raw booking
    lead must still reach the owner instead of silently vanishing into a
    tier-1 FAQ answer (which pushes nothing)."""
    decision = InquiryDecision(
        action_type="push_to_owner_only",
        owner_push_text="(owner push)",
        log_payload={"inquiry_intent": "booking_question"},
        parsed_as_inquiry=True,
    )
    result = _composer().compose(
        message=_message(_FORM_REPLY_TEXT), decision=decision, state=None,
    )

    assert result.owner_push_text == "(owner push)"
    assert result.text is None


def test_gate3_checkout_no_question_mark_answers_from_config() -> None:
    """「幾點退房」has no 嗎/? but is bare checkout FAQ, so gate3 answers."""
    result = _composer().compose(
        message=_message("幾點退房"),
        decision=_non_inquiry_decision(),
        state=None,
    )
    assert result.text == render_faq_checkout(check_in_after="15:00", checkout_before="11:00")
    assert result.owner_push_text is None
    assert result.completed_state_id is None


def test_regression_checkout_price_query_not_hijacked_by_gate3() -> None:
    """⚠️ M1/M2.x regression guard: 「5/14 退房 多少錢」hits checkout tier-1
    but checkout gate3 excludes price intent → falls through → price path."""
    result = _composer().compose(
        message=_message("5/14 退房 多少錢"),
        decision=_price_intent_decision(),
        state=None,
    )
    assert result.text == "PER_MESSAGE_PRICE_REPLY"
    assert result.owner_push_text is None
    assert result.completed_state_id is None


def test_regression_checkout_date_without_price_not_hijacked_by_gate3() -> None:
    """「3/17退房」has unknown intent like bare checkout, but parsed dates keep
    checkout gate3 excluded so it falls through to the existing non-inquiry path."""
    decision = _non_inquiry_decision(
        {
            "inquiry_intent": "unknown",
            "parsed_checkin": "2026-03-17",
            "parsed_checkout": "2026-03-17",
        }
    )
    result = _composer().compose(
        message=_message("3/17退房"),
        decision=decision,
        state=None,
    )
    assert result.text is None
    assert result.owner_push_text is None
    assert result.completed_state_id is None


def test_regression_checkout_no_question_mark_with_active_state_not_hijacked_by_gate3() -> None:
    """A bare checkout FAQ during an active quote state stays in the quote flow."""
    state = _state(checkin_date="2026-05-12", checkout_date=None, adult_count=4)
    result = _composer().compose(
        message=_message("幾點退房"),
        decision=_non_inquiry_decision(),
        state=state,
    )
    assert result.text == SINGLE_MISSING_CHECKOUT_MESSAGE
    assert result.owner_push_text is None
    assert result.completed_state_id is None


def test_regression_checkout_no_question_mark_answers_from_gate3() -> None:
    """「幾點退房」has no 嗎/? and no quote context, so checkout gate3 now answers."""
    result = _composer().compose(
        message=_message("幾點退房"),
        decision=_non_inquiry_decision(),
        state=None,
    )
    assert result.text == render_faq_checkout(check_in_after="15:00", checkout_before="11:00")
    assert result.owner_push_text is None


# ============================================================
# LAYER 2: stale off-mode accumulation reconfirmation gate
# ============================================================

_NOW = datetime(2026, 5, 12, 23, 30, tzinfo=timezone.utc)


def _stale_state(**overrides) -> dict:
    base = _state(
        checkin_date="2026-05-12", checkout_date="2026-05-13", adult_count=4, room_count=2,
        accumulated_while_off=1,
        last_off_mode_update_at=(_NOW - timedelta(minutes=25)).isoformat(),
    )
    base.update(overrides)
    return base


def test_stale_off_accumulation_returns_nudge_not_quote() -> None:
    result = _composer(now_provider=lambda: _NOW).compose(
        message=_message(), decision=_decision(), state=_stale_state()
    )

    assert result.text == render_reconfirm_stale_context_message()
    assert result.reconfirm_shown_state_id == _stale_state()["id"]
    assert result.completed_state_id is None


def test_recent_off_accumulation_within_grace_period_quotes_normally() -> None:
    state = _stale_state(last_off_mode_update_at=(_NOW - timedelta(minutes=5)).isoformat())

    result = _composer(now_provider=lambda: _NOW).compose(
        message=_message(), decision=_decision(), state=state
    )

    assert result.text != render_reconfirm_stale_context_message()
    assert result.reconfirm_shown_state_id is None


def test_flag_false_never_nudges_even_if_timestamp_old() -> None:
    state = _stale_state(accumulated_while_off=0)

    result = _composer(now_provider=lambda: _NOW).compose(
        message=_message(), decision=_decision(), state=state
    )

    assert result.text != render_reconfirm_stale_context_message()
    assert result.reconfirm_shown_state_id is None


def test_completes_conversation_state_bypasses_reconfirm_gate() -> None:
    """A message that is itself self-contained and complete should quote from
    ITS OWN freshly-parsed data, not get nudged over stale accumulated state."""
    state = _stale_state()
    decision = InquiryDecision(
        action_type="reply_to_customer_only",
        customer_reply_text="FRESH_QUOTE",
        log_payload={"a": 1},
        completes_conversation_state=True,
    )

    result = _composer(now_provider=lambda: _NOW).compose(
        message=_message(), decision=decision, state=state
    )

    assert result.text == "FRESH_QUOTE"
    assert result.completed_state_id == state["id"]
