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
    render_over_capacity_message,
    render_quote_message,
)
from app.domain.reply_text import SINGLE_MISSING_GUEST_COUNT_MESSAGE
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
}


def _composer() -> ConversationReplyComposer:
    return ConversationReplyComposer(
        tenant_pricing_loader=lambda tid: _PRICING,
        tenant_special_dates_loader=lambda tid: {},
    )


def _message() -> InboundMessage:
    return InboundMessage(
        tenant_id=1, tenant_slug="t", tenant_timezone="Asia/Taipei",
        platform="line", platform_user_id="Uguest", text="x",
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
