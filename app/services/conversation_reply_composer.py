"""
ConversationReplyComposer — STAGE C: turn the ACCUMULATED conversation state
into the customer reply, so a multi-turn flow that only completes across several
messages still produces a quote (the goldfish-memory payoff).

The per-message InquiryDecision answered one message in isolation. This composer
re-decides the reply from the merged state:
  - no active state            -> the per-message reply (today's behavior, intact)
  - off mode / urgent message  -> the per-message reply (stay silent / un-hijacked)
  - active state, still missing -> ask for the missing slot (accumulated missing)
  - active state, complete      -> quote from the accumulated slots, mark complete

It reuses the SAME domain functions as InquiryService — compute_missing_fields,
calculate_price, render_quote_message, render_missing_info_message — so the
quote/missing logic never diverges from the single-message path.

Allowed imports: stdlib, pydantic, app.domain.*, app.schemas. Like InquiryService
it is forbidden the repository layer; it receives the state row as a plain dict
and returns a ComposedReply for the route to send/act on.
"""

from datetime import date
from typing import Callable

from pydantic import BaseModel

from app.domain.inquiry_completeness import compute_missing_fields
from app.domain.inquiry_decision import InquiryDecision
from app.domain.pricing_models import PricingResult
from app.domain.pricing_policy import calculate_price
from app.domain.reply_templates import (
    render_invalid_date_message,
    render_missing_info_message,
    render_over_capacity_message,
    render_quote_message,
)
from app.schemas import InboundMessage


class ComposedReply(BaseModel):
    """What the route should do: send `text` (None = stay silent), then — only
    when `completed_state_id` is set — best-effort mark that state completed."""

    text: str | None = None
    completed_state_id: int | None = None


class ConversationReplyComposer:
    def __init__(
        self,
        *,
        tenant_pricing_loader: Callable[[int], dict],
        tenant_special_dates_loader: Callable[[int], dict],
    ) -> None:
        self._pricing_loader = tenant_pricing_loader
        self._special_dates_loader = tenant_special_dates_loader

    def compose(
        self,
        *,
        message: InboundMessage,
        decision: InquiryDecision,
        state: dict | None,
    ) -> ComposedReply:
        """Pick the reply: state-driven when an active state is gated on, else
        the per-message fallback."""
        if state is None or decision.was_system_off or decision.was_urgent:
            return ComposedReply(text=decision.customer_reply_text)
        missing = self._missing_for_state(state)
        if missing:
            return ComposedReply(text=_render_missing(missing))
        return ComposedReply(
            text=self._quote_for_state(message, state),
            completed_state_id=state["id"],
        )

    def _missing_for_state(self, state: dict) -> list[str]:
        return compute_missing_fields(
            checkin_date=state["checkin_date"],
            checkout_date=state["checkout_date"],
            guest_count=_state_guest_count(state),
            has_pet=bool(state["has_pet"]),
            pet_count=state["pet_count"],
        )

    def _quote_for_state(self, message: InboundMessage, state: dict) -> str:
        kwargs = _state_stay_kwargs(state)
        pricing = calculate_price(
            **kwargs,
            tenant_pricing=self._pricing_loader(message.tenant_id),
            tenant_special_dates=self._special_dates_loader(message.tenant_id),
        )
        if not pricing.can_quote:
            return _unquotable_reply(pricing)
        return render_quote_message(pricing=pricing, **kwargs)


def _state_guest_count(state: dict) -> int | None:
    """Derive guest_count from the stored adult/child slots (no guest_count
    column exists — total-guests-only messages stay unsupported, by design)."""
    return ((state["adult_count"] or 0) + (state["child_count"] or 0)) or None


def _state_stay_kwargs(state: dict) -> dict:
    """State slots -> calculate_price/render_quote_message kwargs (mirrors
    InquiryService._stay_kwargs: parse dates, default the optionals to 0)."""
    return {
        "checkin_date": date.fromisoformat(state["checkin_date"]),
        "checkout_date": date.fromisoformat(state["checkout_date"]),
        "adult_count": state["adult_count"],
        "child_count": state["child_count"] or 0,
        "infant_count": state["infant_count"] or 0,
        "pet_count": state["pet_count"] or 0,
    }


def _render_missing(missing: list[str]) -> str:
    return render_missing_info_message(
        missing_checkin="checkin_date" in missing,
        missing_checkout="checkout_date" in missing,
        missing_guest_count="guest_count" in missing,
        missing_pet_count="pet_count" in missing,
    )


def _unquotable_reply(pricing: PricingResult) -> str:
    """Mirror InquiryService._unquotable_reply for a complete-but-unquotable
    accumulated state (e.g. over-capacity), so we answer instead of crashing."""
    if "exceeds_max_capacity" in pricing.reasons:
        return render_over_capacity_message()
    return render_invalid_date_message()
