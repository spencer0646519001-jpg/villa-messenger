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

from app.domain.faq_matcher import FaqMatch, match_faq
from app.domain.inquiry_completeness import compute_missing_fields
from app.domain.inquiry_decision import InquiryDecision
from app.domain.pricing_models import PricingResult
from app.domain.pricing_policy import calculate_price
from app.domain.reply_templates import (
    render_faq_breakfast,
    render_faq_checkout,
    render_faq_confirm_and_defer,
    render_faq_pets,
    render_invalid_date_message,
    render_missing_info_message,
    render_over_capacity_message,
    render_owner_push_uncategorized,
    render_quote_message,
)
from app.domain.reply_text import (
    FAQ_FALLBACK_LEAD,
    FAQ_PARKING_LEAD,
    FAQ_WIFI_LEAD,
)
from app.domain.text_normalizer import normalize_for_parsing
from app.schemas import InboundMessage

# confirm-and-defer lead per tier-2 topic; non-whitelist faq uses the fallback.
_DEFER_LEADS: dict[str, str] = {"wifi": FAQ_WIFI_LEAD, "parking": FAQ_PARKING_LEAD}


class ComposedReply(BaseModel):
    """What the route should do, described declaratively (the composer does NO
    I/O -- the route delivers).

      - send `text` (None = stay silent);
      - when `owner_push_text` is set, the route pushes it to the owner FIRST,
        and -- because "已通知" must be truthful -- swaps in `push_failed_text`
        as the customer reply if (and only if) that push fails;
      - when `completed_state_id` is set, best-effort mark that state completed.

    FAQ replies never set `completed_state_id` (FAQ does not touch quote state)."""

    text: str | None = None
    owner_push_text: str | None = None
    push_failed_text: str | None = None
    completed_state_id: int | None = None


class ConversationReplyComposer:
    def __init__(
        self,
        *,
        tenant_pricing_loader: Callable[[int], dict],
        tenant_special_dates_loader: Callable[[int], dict],
        tenant_stay_policy_loader: Callable[[int], dict],
    ) -> None:
        self._pricing_loader = tenant_pricing_loader
        self._special_dates_loader = tenant_special_dates_loader
        self._stay_policy_loader = tenant_stay_policy_loader

    def compose(
        self,
        *,
        message: InboundMessage,
        decision: InquiryDecision,
        state: dict | None,
    ) -> ComposedReply:
        """Pick the reply. Order: off/urgent stay silent; whitelist FAQ answers
        (BEFORE the quote path, so a mid-quote FAQ question never re-quotes nor
        completes the open state); then the state-driven quote/missing path."""
        if decision.was_system_off or decision.was_urgent:
            return ComposedReply(text=decision.customer_reply_text)
        if _is_faq(decision):
            return self._compose_faq(message)
        if state is None:
            return ComposedReply(text=decision.customer_reply_text)
        missing = self._missing_for_state(state)
        if missing:
            return ComposedReply(text=_render_missing(missing))
        return ComposedReply(
            text=self._quote_for_state(message, state),
            completed_state_id=state["id"],
        )

    def _compose_faq(self, message: InboundMessage) -> ComposedReply:
        """A faq-intent message: tier-1 answers from config (no push); tier-2 and
        non-whitelist faq confirm-and-defer + a REAL owner push."""
        faq = match_faq(normalize_for_parsing(message.text))
        if faq is not None and faq.tier == 1:
            return ComposedReply(text=self._tier1_answer(message.tenant_id, faq.topic))
        lead = _defer_lead(faq)
        return ComposedReply(
            text=render_faq_confirm_and_defer(lead=lead, notified=True),
            push_failed_text=render_faq_confirm_and_defer(lead=lead, notified=False),
            owner_push_text=render_owner_push_uncategorized(
                original_text=message.text,
                display_name=message.customer_display_name,
                customer_was_replied=True,  # the confirm-and-defer reply DID go out
            ),
        )

    def _tier1_answer(self, tenant_id: int, topic: str) -> str:
        if topic == "breakfast":
            sp = self._stay_policy_loader(tenant_id)
            return render_faq_breakfast(breakfast_provided=bool(sp.get("breakfast_provided")))
        if topic == "checkout":
            sp = self._stay_policy_loader(tenant_id)
            return render_faq_checkout(
                check_in_after=sp.get("check_in_after"),
                checkout_before=sp.get("checkout_before"),
            )
        pets = self._pricing_loader(tenant_id).get("pets") or {}
        return render_faq_pets(
            allowed_with_notice=bool(pets.get("allowed_with_notice")),
            small_dogs_only=bool(pets.get("small_dogs_only_for_now")),
            fee_twd_per_pet=pets.get("fee_twd_per_pet_per_stay") or 0,
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


def _is_faq(decision: InquiryDecision) -> bool:
    """Key the FAQ branch on the per-message intent, NOT on match_faq alone:
    intent classification already runs price>availability>booking>faq, so a
    quote inquiry that merely mentions a whitelist word (e.g. "早餐多少錢嗎"
    -> price) never enters here and is never hijacked."""
    return decision.log_payload.get("inquiry_intent") == "faq"


def _defer_lead(faq: FaqMatch | None) -> str:
    """Tier-2 topic -> its lead; non-whitelist faq (faq is None) -> fallback."""
    if faq is None:
        return FAQ_FALLBACK_LEAD
    return _DEFER_LEADS[faq.topic]


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
